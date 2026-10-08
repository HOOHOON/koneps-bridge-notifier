import urllib.parse
import urllib.request
import json
import zipfile
import zlib
import re
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Try importing olefile and pypdf if available
try:
    import olefile
except ImportError:
    olefile = None

try:
    import pypdf
except ImportError:
    pypdf = None

def download_file(url, save_path):
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            with open(save_path, 'wb') as f:
                f.write(content)
            return True
    except Exception as e:
        print(f"Download error [{url}]: {e}")
        return False

def extract_text_hwpx(file_path):
    text_content = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            for name in z.namelist():
                if name.startswith('Contents/section') and name.endswith('.xml'):
                    xml_bytes = z.read(name)
                    # Extract text inside xml tags
                    xml_str = xml_bytes.decode('utf-8', errors='ignore')
                    # Strip tags using regex
                    text_only = re.sub(r'<[^>]+>', ' ', xml_str)
                    text_content.append(text_only)
    except Exception as e:
        print(f"HWPX parse error [{file_path}]: {e}")
    return " ".join(text_content)

def extract_text_hwp(file_path):
    text_content = []
    if not olefile:
        print("olefile package not installed. Installing or fallback needed.")
        return ""
    try:
        ole = olefile.OleFileIO(file_path)
        dirs = ole.listdir()
        for d in dirs:
            if d[0] == 'BodyText':
                stream = ole.openstream(d)
                data = stream.read()
                try:
                    decompressed = zlib.decompress(data, -15)
                    # Decode UTF-16LE text
                    text = decompressed.decode('utf-16le', errors='ignore')
                    # Clean control characters
                    clean_text = re.sub(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]', ' ', text)
                    text_content.append(clean_text)
                except Exception:
                    pass
        ole.close()
    except Exception as e:
        print(f"HWP parse error [{file_path}]: {e}")
    return " ".join(text_content)

def extract_text_pdf(file_path):
    text_content = []
    if not pypdf:
        return ""
    try:
        reader = pypdf.PdfReader(file_path)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_content.append(t)
    except Exception as e:
        print(f"PDF parse error [{file_path}]: {e}")
    return " ".join(text_content)

def analyze_structural_engineer(text):
    """
    '구조' 및 '책임기술인' / '구조분야' / '구조기술사' 관련 단어가 포함되어 있는지 검사.
    """
    keywords = ['구조분야', '구조 분야', '구조책임', '구조 책임', '구조기술사', '구조 기술사', '구조 전문', '구조전문']
    
    matches = []
    lines = text.splitlines()
    if len(lines) == 1:
        # If text is space separated single string
        lines = re.split(r'[\.\?\!\n\r\t]', text)

    for line in lines:
        line_strip = line.strip()
        if any(kw in line_strip for kw in keywords):
            # check if it also relates to 책임기술인 / 사업수행능력 / 기술자 / 참여기술인 / 자격
            if any(rel in line_strip for rel in ['책임', '기술자', '기술인', '자격', '평가', '배점', '분야', '담당']):
                matches.append(line_strip[:120])
    
    if matches:
        return True, matches[:3]  # Max 3 matched excerpts
    
    # Simple keyword fallback
    for kw in ['구조분야', '구조책임기술인', '구조기술사']:
        if kw in text:
            return True, [f"키워드 '{kw}' 발견됨"]
            
    return False, []

# Test downloading and analyzing real KONEPS attachments
test_items = [
    ("https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01758513&bidPbancOrd=000&fileType=&fileSeq=1&prcmBsneSeCd=03", "test1.hwpx"),
    ("https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01759835&bidPbancOrd=000&fileType=&fileSeq=2&prcmBsneSeCd=03", "test2.hwpx"),
    ("https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK01752064&bidPbancOrd=000&fileType=&fileSeq=1&prcmBsneSeCd=05", "test3.hwp"),
]

for url, fname in test_items:
    print(f"\nDownloading {fname}...")
    if download_file(url, fname):
        text = ""
        if fname.endswith('.hwpx'):
            text = extract_text_hwpx(fname)
        elif fname.endswith('.hwp'):
            text = extract_text_hwp(fname)
        elif fname.endswith('.pdf'):
            text = extract_text_pdf(fname)
        
        print(f"Extracted text length: {len(text)} chars")
        has_structural, excerpts = analyze_structural_engineer(text)
        print(f"Structural Engineer Required?: {has_structural}")
        if has_structural:
            print("Matching excerpts:")
            for exc in excerpts:
                print(f"  - {exc}")
        
        # Cleanup
        if os.path.exists(fname):
            os.remove(fname)
