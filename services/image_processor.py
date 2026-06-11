import cv2
import numpy as np
import logging
import os

try:
    import mediapipe as mp
except ImportError:
    mp = None

FACE_POINTS = {
    "LIP_UPPER": [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 308, 415, 310, 312, 13, 82, 81, 80, 191, 78],
    "LIP_LOWER": [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 308, 324, 402, 317, 14, 87, 178, 88, 95, 78, 61],
    "EYEBROW_LEFT": [55, 107, 66, 105, 63, 70, 46, 53, 52, 65, 55],
    "EYEBROW_RIGHT": [285, 336, 296, 334, 293, 300, 276, 283, 295, 285],
    "EYELINER_LEFT": [243, 112, 26, 22, 23, 24, 110, 25, 226, 130, 33, 7, 163, 144, 145, 153, 154, 155, 133, 243],
    "EYELINER_RIGHT": [463, 362, 382, 381, 380, 374, 373, 390, 249, 263, 359, 446, 255, 339, 254, 253, 252, 256, 341, 463],
    "EYESHADOW_LEFT": [226, 247, 30, 29, 27, 28, 56, 190, 243, 173, 157, 158, 159, 160, 161, 246, 33, 130, 226],
    "EYESHADOW_RIGHT": [463, 414, 286, 258, 257, 259, 260, 467, 446, 359, 263, 466, 388, 387, 386, 385, 384, 398, 362, 463],
}

class ImageProcessor:
    def __init__(self):
        self.face_mesh = None
        self.face_landmarker = None
        if mp is not None and hasattr(mp, "solutions"):
            self.face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5
            )
        elif mp is not None:
            model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "face_landmarker.task")
            if os.path.exists(model_path):
                try:
                    from mediapipe.tasks import python
                    from mediapipe.tasks.python import vision

                    base_options = python.BaseOptions(model_asset_path=model_path)
                    options = vision.FaceLandmarkerOptions(
                        base_options=base_options,
                        output_face_blendshapes=False,
                        output_facial_transformation_matrixes=False,
                        num_faces=1
                    )
                    self.face_landmarker = vision.FaceLandmarker.create_from_options(options)
                except Exception as e:
                    logging.warning(f"MediaPipe Tasks face landmarker unavailable: {e}")
            else:
                logging.warning("MediaPipe face_landmarker.task not found; virtual makeup landmarks are disabled")
        print("ImageProcessor: Ready.")

    def create_skin_mask(self, img):
        """
        Create a skin mask using YCrCb color space.
        Cr: 135-180, Cb: 85-135
        """
        img_ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        
        # Define range for skin color in YCrCb
        lower_skin = np.array([0, 135, 85], dtype=np.uint8)
        upper_skin = np.array([255, 180, 135], dtype=np.uint8)
        
        mask = cv2.inRange(img_ycrcb, lower_skin, upper_skin)
        
        # Refine mask (Morphology) to fill holes/remove noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.erode(mask, kernel, iterations=1)
        mask = cv2.dilate(mask, kernel, iterations=2)
        
        # Blur the mask for soft edges
        mask = cv2.GaussianBlur(mask, (5, 5), 0)
        return mask

    def apply_smart_smoothing(self, img, mask, d=15, sigmaColor=35, sigmaSpace=35, opacity=0.7):
        """
        Apply Bilateral Filter only to skin area.
        """
        # Bilateral Filter
        smooth = cv2.bilateralFilter(img, d=d, sigmaColor=sigmaColor, sigmaSpace=sigmaSpace)
        
        # Blend based on mask
        # Convert mask to float 0-1
        mask_f = mask.astype(float) / 255.0
        # Expand dims for broadcasting
        mask_f = mask_f[:, :, np.newaxis]
        
        # Blend: Skin gets Smooth, Non-Skin gets Original
        # Result = Smooth * Mask + Original * (1 - Mask)
        # But we also have an global 'opacity' for the effect strength
        
        # Effective mask
        eff_mask = mask_f * opacity
        
        result = (smooth * eff_mask + img * (1.0 - eff_mask)).astype(np.uint8)
        return result

    def adjust_skin_tone(self, img, mask):
        """
        Brighten skin tone: Orange channel Saturation -5, Lightness +8.
        """
        # Convert to HLS (Hue, Lightness, Saturation)
        hls = cv2.cvtColor(img, cv2.COLOR_BGR2HLS)
        h, l, s = cv2.split(hls)
        
        # Skin hue usually around 0-20 (Red-Orange). OpenCV H is 0-180.
        # We target Orange roughly. 
        # But since we have a 'mask', we can just apply to the masked area!
        
        # Convert to int16 to avoid overflow
        s = s.astype(np.int16)
        l = l.astype(np.int16)
        
        # Saturation -5
        s = s - 5
        s = np.clip(s, 0, 255)
        
        # Lightness +8
        l = l + 8
        l = np.clip(l, 0, 255)
        
        # Back to uint8
        s = s.astype(np.uint8)
        l = l.astype(np.uint8)
        
        # Merge
        hls_new = cv2.merge([h, l, s])
        img_new = cv2.cvtColor(hls_new, cv2.COLOR_HLS2BGR)
        
        # Apply only to mask
        mask_f = mask.astype(float) / 255.0
        mask_f = mask_f[:, :, np.newaxis]
        
        final = (img_new * mask_f + img * (1.0 - mask_f)).astype(np.uint8)
        return final

    def apply_tone_curve(self, img):
        """
        Lift midtones (~+5%), reduce highlights slightly.
        Simple implementation using LookUp Table (LUT).
        """
        # Create user curve
        # Points: (0,0), (128, 135) [Lift mid], (200, 195) [Damp high], (255,255)
        # Interpolate
        x_points = [0, 128, 200, 255]
        y_points = [0, 135, 195, 255]
        
        lut = np.interp(np.arange(256), x_points, y_points).astype(np.uint8)
        
        result = cv2.LUT(img, lut)
        return result

    def apply_soft_glow(self, img, opacity=0.1, radius=3):
        """
        Soft Glow: Gaussian Blur overlay with opacity.
        """
        if radius % 2 == 0:
            radius += 1 # Ensure odd for kernel size if using as ksize, but here we use sigma.
            
        # We use radius as sigmaX for GaussianBlur if ksize is (0,0)
        # Or we can use it as ksize index.
        # Let's use it as sigmaX for smoother control.
        glow = cv2.GaussianBlur(img, (0, 0), sigmaX=radius) 
        
        result = cv2.addWeighted(img, 1.0, glow, opacity, 0)
        return result

    def apply_pink_tint(self, img):
        """
        Slight Pink/Magenta tint for Korean style.
        Increase Red and Blue (Magenta) slightly.
        """
        # Add slight constant to Red and Blue channels
        # BGR
        table_r = np.array([min(255, i + 3) for i in range(256)]).astype("uint8")
        table_b = np.array([min(255, i + 2) for i in range(256)]).astype("uint8")
        
        b, g, r = cv2.split(img)
        r = cv2.LUT(r, table_r)
        b = cv2.LUT(b, table_b) # Blue contributes to Magenta
        
        return cv2.merge([b, g, r])

    def apply_baby_tone(self, img):
        """
        Baby Soft Tone: Red +3, Magenta +4 (R+4, B+4) -> Total R+7, B+4.
        """
        table_r = np.array([min(255, i + 7) for i in range(256)]).astype("uint8")
        table_b = np.array([min(255, i + 4) for i in range(256)]).astype("uint8")
        
        b, g, r = cv2.split(img)
        r = cv2.LUT(r, table_r)
        b = cv2.LUT(b, table_b)
        
        return cv2.merge([b, g, r])

    def apply_makeup_light_tone(self, img, mask):
        """
        Light makeup tone: brighten skin slightly and add a subtle healthy tint.
        """
        hls = cv2.cvtColor(img, cv2.COLOR_BGR2HLS)
        h, l, s = cv2.split(hls)

        l = np.clip(l.astype(np.int16) + 6, 0, 255).astype(np.uint8)
        s = np.clip(s.astype(np.int16) + 3, 0, 255).astype(np.uint8)

        toned = cv2.cvtColor(cv2.merge([h, l, s]), cv2.COLOR_HLS2BGR)
        b, g, r = cv2.split(toned)
        r = cv2.LUT(r, np.array([min(255, i + 3) for i in range(256)]).astype("uint8"))
        b = cv2.LUT(b, np.array([min(255, i + 1) for i in range(256)]).astype("uint8"))
        toned = cv2.merge([b, g, r])

        mask_f = (mask.astype(float) / 255.0)[:, :, np.newaxis]
        return (toned * mask_f + img * (1.0 - mask_f)).astype(np.uint8)

    def apply_rose_white_tone(self, img, mask):
        """
        Medium rose-white skin tone: bright enough for print, with a natural pink tint.
        """
        hls = cv2.cvtColor(img, cv2.COLOR_BGR2HLS)
        h, l, s = cv2.split(hls)

        l = np.clip(l.astype(np.int16) + 12, 0, 255).astype(np.uint8)
        s = np.clip(s.astype(np.int16) - 2, 0, 255).astype(np.uint8)

        toned = cv2.cvtColor(cv2.merge([h, l, s]), cv2.COLOR_HLS2BGR)
        b, g, r = cv2.split(toned)
        r = cv2.LUT(r, np.array([min(255, i + 5) for i in range(256)]).astype("uint8"))
        b = cv2.LUT(b, np.array([min(255, i + 2) for i in range(256)]).astype("uint8"))
        toned = cv2.merge([b, g, r])

        mask_f = (mask.astype(float) / 255.0)[:, :, np.newaxis]
        return (toned * mask_f + img * (1.0 - mask_f)).astype(np.uint8)

    def _read_face_landmarks(self, img):
        if self.face_mesh is None and self.face_landmarker is None:
            return None

        height, width = img.shape[:2]
        coords = {}

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if self.face_mesh is not None:
            results = self.face_mesh.process(rgb)
            if not results.multi_face_landmarks:
                return None
            landmarks = results.multi_face_landmarks[0].landmark
        else:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
            results = self.face_landmarker.detect(mp_image)
            if not results.face_landmarks:
                return None
            landmarks = results.face_landmarks[0]

        for idx, landmark in enumerate(landmarks):
            x = int(np.clip(landmark.x * width, 0, width - 1))
            y = int(np.clip(landmark.y * height, 0, height - 1))
            coords[idx] = (x, y)
        return coords

    def _blend_feature(self, base, overlay, alpha, coords, point_ids, color, opacity, blur=9):
        points = [coords.get(idx) for idx in point_ids]
        if any(point is None for point in points) or len(points) < 3:
            return

        feature_mask = np.zeros(alpha.shape, dtype=np.uint8)
        cv2.fillPoly(feature_mask, [np.array(points, dtype=np.int32)], 255)

        if blur % 2 == 0:
            blur += 1
        feature_mask = cv2.GaussianBlur(feature_mask, (blur, blur), 0)
        feature_alpha = (feature_mask.astype(np.float32) / 255.0) * opacity

        color_layer = np.zeros_like(base, dtype=np.float32)
        color_layer[:, :] = np.array(color, dtype=np.float32)

        stronger = feature_alpha > alpha
        overlay[stronger] = color_layer[stronger]
        alpha[:] = np.maximum(alpha, feature_alpha)

    def _blend_blush(self, base, overlay, alpha, coords, center_idx, color, opacity):
        center = coords.get(center_idx)
        if center is None:
            return

        height, width = alpha.shape
        radius_x = max(14, int(width * 0.045))
        radius_y = max(10, int(height * 0.035))
        feature_mask = np.zeros(alpha.shape, dtype=np.uint8)
        cv2.ellipse(feature_mask, center, (radius_x, radius_y), 0, 0, 360, 255, -1)
        feature_mask = cv2.GaussianBlur(feature_mask, (41, 41), 0)
        feature_alpha = (feature_mask.astype(np.float32) / 255.0) * opacity

        color_layer = np.zeros_like(base, dtype=np.float32)
        color_layer[:, :] = np.array(color, dtype=np.float32)

        stronger = feature_alpha > alpha
        overlay[stronger] = color_layer[stronger]
        alpha[:] = np.maximum(alpha, feature_alpha)

    def apply_virtual_makeup(self, img):
        coords = self._read_face_landmarks(img)
        if coords is None:
            return img, False

        base = img.astype(np.float32)
        overlay = base.copy()
        alpha = np.zeros(img.shape[:2], dtype=np.float32)

        style = {
            "LIP_UPPER": ([84, 66, 188], 0.34),
            "LIP_LOWER": ([84, 66, 188], 0.30),
            "EYEBROW_LEFT": ([36, 42, 58], 0.20),
            "EYEBROW_RIGHT": ([36, 42, 58], 0.20),
            "EYELINER_LEFT": ([38, 36, 54], 0.24),
            "EYELINER_RIGHT": ([38, 36, 54], 0.24),
            "EYESHADOW_LEFT": ([154, 124, 178], 0.16),
            "EYESHADOW_RIGHT": ([154, 124, 178], 0.16),
        }

        for name, (color, opacity) in style.items():
            self._blend_feature(base, overlay, alpha, coords, FACE_POINTS[name], color, opacity)

        self._blend_blush(base, overlay, alpha, coords, 50, [132, 124, 210], 0.18)
        self._blend_blush(base, overlay, alpha, coords, 280, [132, 124, 210], 0.18)

        alpha = np.clip(alpha, 0.0, 0.45)
        result = overlay * alpha[:, :, None] + base * (1.0 - alpha[:, :, None])
        return np.clip(result, 0, 255).astype(np.uint8), True

    def process(self, image_path, output_path, filter_type='natural'):
        try:
            img = cv2.imread(image_path)
            if img is None:
                return False, "Image not found"
            
            # 1. Skin Mask
            skin_mask = self.create_skin_mask(img)
            
            # Settings based on type
            if filter_type == 'men':
                d = 10
                sigma = 20
                opacity = 0.4 
            elif filter_type == 'makeup_soft':
                d = 13
                sigma = 36
                opacity = 0.62
            elif filter_type == 'makeup_light':
                d = 13
                sigma = 32
                opacity = 0.55
            elif filter_type == 'korean':
                d = 13
                sigma = 36
                opacity = 0.58
            elif filter_type == 'baby_soft':
                d = 15
                sigma = 40 # smooth
                opacity = 0.7 # 70%
            else: # natural
                d = 15
                sigma = 35
                opacity = 0.7
            
            # 2. Smoothing
            # Baby soft uses sigmaSpace 35, sigmaColor 40.
            # Our func uses same for both usually. Let's customize if needed.
            # apply_smart_smoothing(self, img, mask, d=15, sigmaColor=35, sigmaSpace=35, opacity=0.7)
            if filter_type == 'baby_soft':
                 img = self.apply_smart_smoothing(img, skin_mask, d=d, sigmaColor=40, sigmaSpace=35, opacity=opacity)
            else:
                 img = self.apply_smart_smoothing(img, skin_mask, d=d, sigmaColor=sigma, sigmaSpace=sigma, opacity=opacity)
            
            # 3. Tone / Tint
            if filter_type == 'natural':
                img = self.adjust_skin_tone(img, skin_mask)
            elif filter_type == 'makeup_soft':
                img = self.apply_makeup_light_tone(img, skin_mask)
                img, has_makeup = self.apply_virtual_makeup(img)
                if not has_makeup:
                    logging.warning("MediaPipe makeup skipped: no face detected or mediapipe unavailable")
            elif filter_type == 'makeup_light':
                img = self.apply_makeup_light_tone(img, skin_mask)
            elif filter_type == 'korean':
                img = self.apply_rose_white_tone(img, skin_mask)
            elif filter_type == 'baby_soft':
                # "Lift midtone" - done by global tone curve later?
                # "Pink tone (cực nhẹ)"
                img = self.apply_baby_tone(img)
            
            # 4. Tone Curve (Global)
            img = self.apply_tone_curve(img)
            
            # 5. Glow
            if filter_type == 'makeup_soft':
                img = self.apply_soft_glow(img, opacity=0.05, radius=3)
            elif filter_type == 'makeup_light':
                img = self.apply_soft_glow(img, opacity=0.06, radius=3)
            elif filter_type == 'korean':
                img = self.apply_soft_glow(img, opacity=0.06, radius=3)
            elif filter_type == 'baby_soft':
                # Radius 12-18 -> let's say 15. Opacity 8-12% -> 0.10
                img = self.apply_soft_glow(img, opacity=0.10, radius=15)
            else:
                img = self.apply_soft_glow(img, opacity=0.12, radius=3)
            
            cv2.imwrite(output_path, img)
            return True, output_path
            
        except Exception as e:
            logging.error(f"ImageProcessor Error: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False, str(e)

processor = None

def get_processor():
    global processor
    if processor is None:
        processor = ImageProcessor()
    return processor
