import urllib.request
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText
import io
import mimetypes
import uuid

def encode_multipart_formdata(fields, files):
    boundary = uuid.uuid4().hex
    body = bytearray()
    
    for key, value in fields.items():
        body.extend(f'--{boundary}\r\n'.encode('utf-8'))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode('utf-8'))
        body.extend(f'{value}\r\n'.encode('utf-8'))
        
    for key, (filename, content, content_type) in files.items():
        body.extend(f'--{boundary}\r\n'.format(boundary).encode('utf-8'))
        body.extend(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode('utf-8'))
        body.extend(f'Content-Type: {content_type}\r\n\r\n'.encode('utf-8'))
        body.extend(content)
        body.extend(b'\r\n')
        
    body.extend(f'--{boundary}--\r\n'.encode('utf-8'))
    contentType = f'multipart/form-data; boundary={boundary}'
    
    return contentType, body

try:
    with open('test.webm', 'rb') as f:
        file_content = f.read()

    content_type, body = encode_multipart_formdata({}, {'file': ('test.webm', file_content, 'video/webm')})

    req = urllib.request.Request('http://127.0.0.1:5000/api/convert-motion', data=body, method='POST')
    req.add_header('Content-Type', content_type)
    req.add_header('Accept', 'video/mp4')

    try:
        response = urllib.request.urlopen(req)
        print("Status:", response.status)
        print("Response Length:", len(response.read()))
    except urllib.error.HTTPError as e:
        print("HTTP Error:", e.code)
        print("Error Body:", e.read().decode('utf-8', errors='replace'))
    except Exception as e:
        print("Other Error:", e)

except Exception as e:
    print("Failed to run test:", e)
