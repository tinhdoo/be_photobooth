import cv2
import logging
import traceback
import numpy as np
import torch
from gfpgan import GFPGANer

class AIService:
    def __init__(self):
        print("AI Service (Face Only): Initializing...")
        self.restorer = None
        
        try:
            # Setup GFPGAN v1.4
            # weight: 0.25 - 0.30 as requested.
            # bg_upsampler: None (No RealESRGAN)
            print("AI Service: Loading GFPGAN v1.4 (Face Only)...")
            self.restorer = GFPGANer(
                model_path='https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth',
                upscale=1, 
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=None, # Explicitly None
                device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            )
            print("AI Service: Face Only Pipeline Ready.")
            
        except Exception as e:
            import traceback
            logging.error(f"AI Service Init Error: {e}")
            logging.error(traceback.format_exc())
            print(f"AI Service Init Error: {e}")

    def enhance_image(self, image_path, output_path):
        if not self.restorer:
            return False, "AI Model not initialized"

        try:
            img = cv2.imread(image_path, cv2.IMREAD_COLOR)
            if img is None:
                return False, "Image not found"

            print(f"Enhancing {image_path} with GFPGAN (Face Only)...")
            
            # restore_face (cropped_faces, restored_faces, restored_img)
            # weight: 0.3 (in range 0.25-0.30)
            _, _, restored_img = self.restorer.enhance(
                img,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
                weight=0.3 
            )

            if restored_img is not None:
                cv2.imwrite(output_path, restored_img)
                return True, output_path
            else:
                return False, "Enhancement failed"
                
        except Exception as e:
            import traceback
            logging.error(f"Error enhancing image: {e}")
            logging.error(traceback.format_exc())
            print(f"Error enhancing image: {e}")
            return False, str(e)

ai_service = AIService()
