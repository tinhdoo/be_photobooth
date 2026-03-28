import cv2
import numpy as np
import logging

class ImageProcessor:
    def __init__(self):
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
            elif filter_type == 'korean':
                d = 15
                sigma = 45 
                opacity = 0.7
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
            elif filter_type == 'korean':
                img = self.adjust_skin_tone(img, skin_mask)
                img = self.apply_pink_tint(img)
            elif filter_type == 'baby_soft':
                # "Lift midtone" - done by global tone curve later?
                # "Pink tone (cực nhẹ)"
                img = self.apply_baby_tone(img)
            
            # 4. Tone Curve (Global)
            img = self.apply_tone_curve(img)
            
            # 5. Glow
            if filter_type == 'korean':
                img = self.apply_soft_glow(img, opacity=0.10, radius=3)
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
