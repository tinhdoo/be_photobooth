
import cv2
import sys

try:
    # Create a dummy video
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter('test_output.mp4', fourcc, 20.0, (640, 480))
    if not out.isOpened():
        print("Error: Could not open video writer for mp4v")
        sys.exit(1)
    out.release()
    print("Success: cv2 can write mp4v")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
