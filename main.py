import os
import sys
import json
import re
import time
import datetime
import urllib.parse
import urllib.request
import smtplib
import zipfile
import zlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# HWP / PDF 파싱용 라이브러리
try:
    import olefile
except ImportError:
    olefile = None

try:
    import pypdf
except ImportError:
    pypdf = None

# UTF-8 출력 설정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# API Endpoints
BID_PUBLIC_BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
PRE_SPEC_BASE_URL = "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService"
ORDER_PLAN_BASE_URL = "https://apis.data.go.kr/1230000/ao/OrderPlanSttusService"

def load_default_service_key():
    txt_files = [f for f in os.listdir('.') if f.endswith('.txt')]
    for tf in txt_files:
        try:
            with open(tf, 'rb') as f:
                content = f.read().decode('utf-8', errors='ignore')
                for line in content.splitlines():
                    line = line.strip()
                    if len(line) == 64 and not line.startswith('http'):
                        return line
        except Exception:
            pass
    return "3f088232ed6232e3ce3362cc35d4ea291f47f1c9bfe102498dedefc05341f6aa"

def get_env_or_default(key, default=""):
    val = os.getenv(key, "").strip()
    return val if val else default

def make_http_request(url, retries=3, timeout=45):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive'
    }

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                header = data.get('response', {}).get('header', {})
                result_code = header.get('resultCode')
                
                if result_code != '00':
                    print(f"⚠️ API Warning: {header.get('resultMsg')}")
                    return []
                    
                items = data.get('response', {}).get('body', {}).get('items', [])
                if isinstance(items, dict):
                    items = [items]
                return items
        except Exception as e:
            print(f"⚠️ Attempt {attempt}/{retries} failed for URL: {e}")
            if attempt < retries:
                time.sleep(2)
    return []

# 1. 입찰공고 수집
def fetch_bid_public(service_key, op_name, keyword, bgn_dt, end_dt):
    params = {
        'serviceKey': service_key,
        'type': 'json',
        'inqryDiv': '1',
        'inqryBgnDt': bgn_dt,
        'inqryEndDt': end_dt,
        'numOfRows': '100',
        'pageNo': '1'
    }
    if keyword:
        params['bidNtceNm'] = keyword
    url = f"{BID_PUBLIC_BASE_URL}/{op_name}?{urllib.parse.urlencode(params)}"
    return make_http_request(url)

# 2. 사전규격공개 수집
def fetch_pre_spec(service_key, op_name, bgn_dt, end_dt):
    params = {
        'serviceKey': service_key,
        'type': 'json',
        'inqryDiv': '1',
        'inqryBgnDt': bgn_dt,
        'inqryEndDt': end_dt,
        'numOfRows': '100',
        'pageNo': '1'
    }
    url = f"{PRE_SPEC_BASE_URL}/{op_name}?{urllib.parse.urlencode(params)}"
    return make_http_request(url)

# 3. 발주계획 수집
def fetch_order_plan(service_key, op_name, keyword, bgn_dt, end_dt):
    params = {
        'serviceKey': service_key,
        'type': 'json',
        'inqryBgnDt': bgn_dt,
        'inqryEndDt': end_dt,
        'numOfRows': '100',
        'pageNo': '1'
    }
    if keyword:
        params['bizNm'] = keyword
    url = f"{ORDER_PLAN_BASE_URL}/{op_name}?{urllib.parse.urlencode(params)}"
    return make_http_request(url)

def safe_int(val):
    if not val and val != 0:
        return 0
    try:
        return int(float(str(val).replace(',', '').strip()))
    except (ValueError, TypeError):
        return 0

def format_price(amount_val):
    if not amount_val and amount_val != 0:
        return "미지정"
    val = safe_int(amount_val)
    if val == 0:
        return "미지정 (0원)"
    if val >= 100000000:
        return f"{val / 100000000:.2f} 억 원 ({val:,} 원)"
    elif val >= 10000:
        return f"{val / 10000:,.0f} 만 원 ({val:,} 원)"
    else:
        return f"{val:,} 원"

def is_target_bridge_title(title):
    if not title:
        return False
    # 학교 / 기타 예외 키워드
    if re.search(r'외\s*\d+\s*교', title) or '향교' in title or '중학' in title:
        return False
    # '교량' 또는 '다리'가 명시되어 있으면 무조건 대상
    if '교량' in title or '다리' in title:
        return True
    
    bridge_pattern = re.compile(r'(?:[가-힣A-Za-z0-9]+교(?=[\s\d_\-\[\(\)\.]|$))')
    matches = bridge_pattern.findall(title)
    false_positives = {
        '교육', '교체', '교류', '교재', '교환', '교통', '교원', '교실', '교구', 
        '향교', '종교', '설교', '불교', '주교', '원교', '비교', '외교', '선교', 
        '전교', '교정', '교화', '교관', '교습', '교도', '조교', '훈교'
    }
    
    for m in matches:
        if m in false_positives:
            continue
        # 학교관련 명칭 제외
        if any(sw in m for sw in ['학교', '초등', '고등', '중등', '대학', '분교', '교사']):
            continue
        return True
    return False

def download_attachment(url, temp_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*'
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read()
            with open(temp_path, 'wb') as f:
                f.write(content)
            return True
    except Exception:
        return False

def extract_text_hwpx(file_path):
    text_content = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            for name in z.namelist():
                if name.startswith('Contents/section') and name.endswith('.xml'):
                    xml_bytes = z.read(name)
                    xml_str = xml_bytes.decode('utf-8', errors='ignore')
                    text_only = re.sub(r'<[^>]+>', ' ', xml_str)
                    text_content.append(text_only)
    except Exception:
        pass
    return " ".join(text_content)

def extract_text_hwp(file_path):
    text_content = []
    if not olefile:
        return ""
    try:
        ole = olefile.OleFileIO(file_path)
        for d in ole.listdir():
            if d[0] == 'BodyText':
                stream = ole.openstream(d)
                data = stream.read()
                try:
                    decompressed = zlib.decompress(data, -15)
                    text = decompressed.decode('utf-8', errors='ignore') if b'\x00' not in decompressed[:20] else decompressed.decode('utf-16le', errors='ignore')
                    clean_text = re.sub(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]', ' ', text)
                    text_content.append(clean_text)
                except Exception:
                    pass
        ole.close()
    except Exception:
        pass
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
    except Exception:
        pass
    return " ".join(text_content)

def get_attachment_urls(item):
    urls = []
    # 1. 입찰공고 첨부파일
    for k in range(1, 11):
        u = item.get(f'ntceSpecDocUrl{k}')
        n = item.get(f'ntceSpecFileNm{k}', f'doc_{k}.hwpx')
        if u:
            urls.append((u, n))
    # 2. 사전규격 첨부파일
    for k in range(1, 6):
        u = item.get(f'specDocFileUrl{k}')
        if u:
            urls.append((u, f'prespec_{k}.hwpx'))
    # 3. 발주계획/기타 첨부파일
    for k in ['specDocOpninFileUrl1', 'orderPlanDtlUrl']:
        u = item.get(k)
        if u and any(ext in u.lower() for ext in ['.hwp', '.pdf', '.hwpx', 'download']):
            urls.append((u, f'other_{k}.hwpx'))
    return urls

def analyze_strict_lead_pm(item):
    pm_keywords = ['사업책임기술인', '사업책임기술자', '사업책임자', '총괄책임기술인', '총괄책임기술자', '사업총괄책임']
    
    known_fields = [
        ('토목구조', ['토목구조', '구조분야', '구조']),
        ('도로·공항', ['도로및공항', '도로·공항', '도로']),
        ('수자원', ['수자원개발', '수자원']),
        ('토질·지질', ['토질지질', '토질·지질', '토질']),
        ('안전진단', ['안전진단']),
        ('시공', ['시공']),
        ('상하수도', ['상하수도']),
        ('도시계획', ['도시계획']),
        ('건축', ['건축']),
        ('전기', ['전기']),
        ('토목일반', ['토목'])
    ]

    pm_lines = []
    attachments = get_attachment_urls(item)

    for idx, (doc_url, doc_name) in enumerate(attachments[:5], 1):
        ext = doc_name.split('.')[-1].lower() if '.' in doc_name else 'hwpx'
        temp_file = f"temp_file_{idx}.{ext}"

        if download_attachment(doc_url, temp_file):
            text = ""
            try:
                with open(temp_file, 'rb') as f:
                    head = f.read(10)
                if head.startswith(b'PK\x03\x04'):
                    text = extract_text_hwpx(temp_file)
                elif olefile and olefile.isOleFile(temp_file):
                    text = extract_text_hwp(temp_file)
                elif head.startswith(b'%PDF'):
                    text = extract_text_pdf(temp_file)
                else:
                    text = extract_text_hwpx(temp_file) or extract_text_hwp(temp_file) or extract_text_pdf(temp_file)
            except Exception:
                pass

            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

            if text:
                lines = re.split(r'[\.\?\!\n\r]', text)
                for line in lines:
                    line_clean = line.strip()
                    if any(pm_kw in line_clean for pm_kw in pm_keywords):
                        if '분야별' in line_clean and '사업책임' not in line_clean:
                            continue
                        pm_lines.append(re.sub(r'\s+', ' ', line_clean)[:120])

    stage = item.get('_stage', '입찰공고')

    if not pm_lines:
        return False, f"미확인 ({stage} 서류 참조)", "첨부 서류 확인 필요"

    detected_field = "미확인"
    is_struct_pm = False

    for l in pm_lines:
        for main_name, synonyms in known_fields:
            if any(syn in l for syn in synonyms):
                detected_field = main_name
                if main_name in ['토목구조', '구조']:
                    is_struct_pm = True
                break
        if detected_field != "미확인":
            break

    excerpt = pm_lines[0] if pm_lines else ""
    return is_struct_pm, detected_field, excerpt

def build_telegram_messages(bids, bgn_date_str):
    c_bid = sum(1 for b in bids if b.get('_stage') == '입찰공고')
    c_prespec = sum(1 for b in bids if b.get('_stage') == '사전규격')
    c_orderplan = sum(1 for b in bids if b.get('_stage') == '발주계획')

    header = (
        f"📢 <b>[조달청 나라장터 교량/다리 통합 48시간 리포트]</b>\n"
        f"📅 수집 기간: {bgn_date_str}\n"
        f"💰 가격 조건: <b>2억 원 이상</b> (발주계획 포함)\n"
        f"📊 <b>단계별 요약</b>: 🔵 입찰공고 <b>{c_bid}건</b> | 🟡 사전규격 <b>{c_prespec}건</b> | 🟣 발주계획 <b>{c_orderplan}건</b> (총 <b>{len(bids)}건</b>)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not bids:
        return [header + "지난 48시간 동안 조건(2억 이상)에 맞는 신규 입찰 공고/계획이 없습니다. 😊"]

    messages = []
    current_msg = header

    for idx, bid in enumerate(bids, 1):
        stage = bid.get('_stage', '입찰공고')
        domain_kr = bid.get('_domain_kr', '기타')
        title = bid.get('_title', '제목 없음').strip()
        bid_no = bid.get('_id', 'N/A')
        instt_nm = bid.get('_instt', '미지정')
        price_str = format_price(bid.get('_price'))
        date_str = bid.get('_date', '미지정')
        detail_url = bid.get('_url', '#')

        is_struct_pm = bid.get('_is_struct_pm', False)
        lead_field = bid.get('_lead_field', '미확인')
        lead_exc = bid.get('_lead_excerpt', '')

        if stage == "입찰공고":
            stage_badge = "🔵 [입찰공고]"
        elif stage == "사전규격":
            stage_badge = "🟡 [사전규격공개]"
        else:
            stage_badge = "🟣 [발주계획현황]"

        if is_struct_pm:
            pm_tag = f"<b>🎯 [구조부 주관 가능] 사업책임기술인: {lead_field}</b>"
        else:
            pm_tag = f"<b>ℹ️ [타 분야 주관] 사업책임기술인: {lead_field}</b>"

        item_str = (
            f"<b>{idx}. {stage_badge} [{domain_kr}] {title}</b>\n"
            f"• <b>구분 (단계)</b>: {stage_badge}\n"
            f"• <b>번호/ID</b>: {bid_no}\n"
            f"• <b>수요기관</b>: {instt_nm}\n"
            f"• <b>추정가격/예산</b>: <b>{price_str}</b>\n"
            f"• <b>일시/기한</b>: <code>{date_str}</code>\n"
            f"• <b>사업책임기술인 분석</b>: {pm_tag}\n"
            f"  <i>({lead_exc[:60]}...)</i>\n"
            f"🔗 <a href='{detail_url}'>나라장터 상세 보기</a>\n\n"
        )

        if len(current_msg) + len(item_str) > 3800:
            messages.append(current_msg)
            current_msg = f"<b>[입찰공고/사전규격/발주계획 알림 (이어서)]</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + item_str
        else:
            current_msg += item_str

    if current_msg:
        messages.append(current_msg)

    return messages

def send_telegram_message(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, 
        data=data, 
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            if res.get('ok'):
                print("✅ 텔레그램 메시지 전송 성공!")
                return True
            else:
                print(f"❌ 텔레그램 전송 실패: {res}")
                return False
    except Exception as e:
        print(f"❌ 텔레그램 API 오류: {e}")
        return False

def send_email_report(smtp_server, smtp_port, smtp_user, smtp_pass, receivers, bids, bgn_date_str):
    if not smtp_user or not smtp_pass or not receivers:
        print("⚠️ SMTP 이메일 설정이 부재하여 이메일 발송을 건너땁니다.")
        return False

    receiver_list = [r.strip() for r in receivers.split(',') if r.strip()]
    if not receiver_list:
        return False

    c_bid = sum(1 for b in bids if b.get('_stage') == '입찰공고')
    c_prespec = sum(1 for b in bids if b.get('_stage') == '사전규격')
    c_orderplan = sum(1 for b in bids if b.get('_stage') == '발주계획')

    subject = f"[조달청 나라장터] 교량/다리 2억 이상 통합 리포트 (입찰공고 {c_bid}건 · 사전규격 {c_prespec}건 · 발주계획 {c_orderplan}건)"

    rows_html = ""
    for idx, bid in enumerate(bids, 1):
        stage = bid.get('_stage', '입찰공고')
        domain_kr = bid.get('_domain_kr', '기타')
        title = bid.get('_title', '제목 없음').strip()
        bid_no = bid.get('_id', 'N/A')
        instt_nm = bid.get('_instt', '미지정')
        price_str = format_price(bid.get('_price'))
        date_str = bid.get('_date', '미지정')
        detail_url = bid.get('_url', '#')

        is_struct_pm = bid.get('_is_struct_pm', False)
        lead_field = bid.get('_lead_field', '미확인')
        lead_exc = bid.get('_lead_excerpt', '')

        if stage == '입찰공고':
            stage_badge = '<span style="background-color:#ebf8ff; color:#2b6cb0; border:1px solid #90cdf4; padding:4px 8px; border-radius:4px; font-weight:bold; font-size:12px;">🔵 입찰공고</span>'
        elif stage == '사전규격':
            stage_badge = '<span style="background-color:#fefcbf; color:#975a16; border:1px solid #f6e05e; padding:4px 8px; border-radius:4px; font-weight:bold; font-size:12px;">🟡 사전규격공개</span>'
        else:
            stage_badge = '<span style="background-color:#faf5ff; color:#6b46c1; border:1px solid #d6bcfa; padding:4px 8px; border-radius:4px; font-weight:bold; font-size:12px;">🟣 발주계획현황</span>'

        if is_struct_pm:
            pm_badge = f'<div style="margin-top:6px; background-color:#c6f6d5; color:#22543d; border:1px solid #9ae6b4; padding:5px 10px; border-radius:4px; font-size:12px; font-weight:bold;">🎯 [구조부 주관 가능] 사업책임기술인 분야: {lead_field} <span style="font-size:11px; font-weight:normal;">({lead_exc[:45]}...)</span></div>'
        else:
            pm_badge = f'<div style="margin-top:6px; background-color:#f7fafc; color:#4a5568; border:1px solid #e2e8f0; padding:4px 8px; border-radius:4px; font-size:12px;">ℹ️ [타 분야 주관] 사업책임기술인 분야: {lead_field} <span style="color:#718096; font-size:11px;">({lead_exc[:45]}...)</span></div>'

        bg_color = "#ffffff" if idx % 2 != 0 else "#f9fbfd"

        rows_html += f"""
        <tr style="background-color: {bg_color}; border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 12px; text-align: center; font-weight: bold; color: #4a5568;">{idx}</td>
            <td style="padding: 12px; text-align: center;">{stage_badge}</td>
            <td style="padding: 12px; text-align: center;">
                <span style="background-color: #edf2f7; color: #4a5568; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{domain_kr}</span>
            </td>
            <td style="padding: 12px;">
                <a href="{detail_url}" target="_blank" style="color: #2b6cb0; text-decoration: none; font-weight: bold;">{title}</a>
                <div style="color: #718096; font-size: 12px; margin-top: 4px;">공고/계획 ID: {bid_no}</div>
                {pm_badge}
            </td>
            <td style="padding: 12px; color: #2d3748; font-weight: 500;">{instt_nm}</td>
            <td style="padding: 12px; text-align: right; color: #2c5282; font-weight: bold;">{price_str}</td>
            <td style="padding: 12px; text-align: center; color: #e53e3e; font-size: 12px; font-family: monospace;">{date_str}</td>
            <td style="padding: 12px; text-align: center;">
                <a href="{detail_url}" target="_blank" style="background-color: #3182ce; color: #ffffff; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-size: 12px; display: inline-block;">상세보기</a>
            </td>
        </tr>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif; background-color: #f7fafc; margin: 0; padding: 20px;">
        <div style="max-width: 1100px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
            <h2 style="color: #1a365d; margin-top: 0; border-bottom: 2px solid #3182ce; padding-bottom: 12px;">
                🌉 조달청 나라장터 교량/다리 통합 48시간 리포트 (2억 원 이상)
            </h2>
            <div style="background-color: #ebf8ff; border-left: 4px solid #3182ce; padding: 14px 18px; margin-bottom: 24px; border-radius: 6px; color: #2c5282;">
                <strong>📅 수집 기간 (최근 48시간):</strong> {bgn_date_str}<br>
                <strong>💰 최소 금액 조건:</strong> <span style="font-weight:bold; color:#e53e3e;">2억 원 이상</span> (발주계획 포함)<br>
                <strong>📊 수집 건수:</strong> 
                <span style="font-size:15px; font-weight:bold; color:#2b6cb0;">🔵 입찰공고 {c_bid}건</span> | 
                <span style="font-size:15px; font-weight:bold; color:#b7791f;">🟡 사전규격 {c_prespec}건</span> | 
                <span style="font-size:15px; font-weight:bold; color:#6b46c1;">🟣 발주계획 {c_orderplan}건</span>
                (총 <strong style="font-size:16px; color:#e53e3e;">{len(bids)}건</strong>)
            </div>

            <table style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px;">
                <thead>
                    <tr style="background-color: #2b6cb0; color: #ffffff;">
                        <th style="padding: 12px; width: 35px;">#</th>
                        <th style="padding: 12px; width: 110px;">구분 (단계)</th>
                        <th style="padding: 12px; width: 75px;">분야</th>
                        <th style="padding: 12px;">공고/사업명 & 사업책임기술인 (TL) 주관 분야 분석</th>
                        <th style="padding: 12px; width: 140px;">수요기관</th>
                        <th style="padding: 12px; width: 140px; text-align: right;">추정가격 / 예산</th>
                        <th style="padding: 12px; width: 130px; text-align: center;">일시/마감일</th>
                        <th style="padding: 12px; width: 80px; text-align: center;">링크</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html if bids else '<tr><td colspan="8" style="padding: 30px; text-align: center; color: #a0aec0;">지난 48시간 동안 조건(2억 이상)에 맞는 신규 입찰 공고/계획이 없습니다.</td></tr>'}
                </tbody>
            </table>

            <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #e2e8f0; color: #a0aec0; font-size: 12px; text-align: center;">
                본 이메일은 GitHub Actions 자동화 시스템에 의해 수신인({', '.join(receiver_list)})에게 매일 발송됩니다.
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = ", ".join(receiver_list)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP_SSL(smtp_server, int(smtp_port)) if int(smtp_port) == 465 else smtplib.SMTP(smtp_server, int(smtp_port))
        if int(smtp_port) != 465:
            server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, receiver_list, msg.as_string())
        server.quit()
        print(f"✅ 이메일 리포트 전송 성공! 수신처: {receiver_list}")
        return True
    except Exception as e:
        print(f"❌ 이메일 전송 실패: {e}")
        return False

def generate_web_dashboard(bids, bgn_date_str):
    data_json = json.dumps(bids, ensure_ascii=False)
    
    c_bid = sum(1 for b in bids if b.get('_stage') == '입찰공고')
    c_prespec = sum(1 for b in bids if b.get('_stage') == '사전규격')
    c_orderplan = sum(1 for b in bids if b.get('_stage') == '발주계획')

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>조달청 나라장터 교량/다리 통합 대시보드 (2억 이상)</title>
    <style>
        * {{ box-sizing: border-box; font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif; }}
        body {{ background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
        header {{ border-bottom: 2px solid #2b6cb0; padding-bottom: 15px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
        h1 {{ color: #1a365d; margin: 0; font-size: 24px; }}
        .sub-header {{ font-size: 13px; color: #718096; }}
        
        .info-bar {{ background: #ebf8ff; color: #2c5282; padding: 14px 18px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; font-weight: 500; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }}
        
        .stat-group {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
        .stat-card {{ padding: 6px 12px; border-radius: 6px; font-size: 13px; font-weight: bold; border: 1px solid transparent; display: flex; align-items: center; gap: 5px; }}
        .stat-card-bid {{ background-color: #eef6ff; color: #2b6cb0; border-color: #bee3f8; }}
        .stat-card-prespec {{ background-color: #fffdf0; color: #975a16; border-color: #f6e05e; }}
        .stat-card-orderplan {{ background-color: #fcfaff; color: #6b46c1; border-color: #e9d8fd; }}
        .stat-card-total {{ background-color: #feefef; color: #e53e3e; border-color: #feb2b2; }}

        .controls {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
        .search-box {{ flex: 1; min-width: 240px; padding: 10px 16px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 14px; outline: none; }}
        .filter-select {{ padding: 10px 14px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 14px; background: #fff; cursor: pointer; }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
        th {{ background: #2b6cb0; color: #fff; padding: 12px; text-align: left; font-weight: 600; }}
        td {{ padding: 12px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }}
        tr:hover {{ background-color: #f7fafc; }}

        .badge {{ display: inline-block; padding: 5px 10px; border-radius: 6px; font-size: 12px; font-weight: bold; text-align: center; white-space: nowrap; }}
        .badge-stage-bid {{ background-color: #ebf8ff; color: #2b6cb0; border: 1px solid #90cdf4; }}
        .badge-stage-prespec {{ background-color: #fefcbf; color: #975a16; border: 1px solid #f6e05e; }}
        .badge-stage-orderplan {{ background-color: #faf5ff; color: #6b46c1; border: 1px solid #d6bcfa; }}
        
        .badge-servc {{ background-color: #edf2f7; color: #4a5568; }}
        .badge-cnstwk {{ background-color: #feebc8; color: #c05621; }}
        
        .pm-badge-struct {{ margin-top: 6px; background-color: #c6f6d5; color: #22543d; border: 1px solid #9ae6b4; padding: 5px 10px; border-radius: 6px; font-size: 12px; font-weight: bold; display: inline-block; }}
        .pm-badge-other {{ margin-top: 6px; background-color: #f7fafc; color: #4a5568; border: 1px solid #e2e8f0; padding: 4px 8px; border-radius: 6px; font-size: 12px; display: inline-block; }}
        .pm-field-name {{ font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-left: 4px; }}

        .price {{ color: #2c5282; font-weight: bold; text-align: right; }}
        .deadline {{ color: #e53e3e; font-size: 13px; font-family: monospace; text-align: center; }}
        .btn-link {{ background-color: #3182ce; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: 500; display: inline-block; transition: background 0.2s; }}
        .btn-link:hover {{ background-color: #2b6cb0; }}
        .title-tag {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-right: 6px; vertical-align: middle; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🌉 조달청 나라장터 교량/다리 통합 대시보드</h1>
                <div class="sub-header">수집 대상: <strong>입찰공고 · 사전규격공개 · 발주계획 (최근 48시간 / 2억 원 이상)</strong></div>
            </div>
            <div style="font-size: 13px; color: #718096;">최종 업데이트: <strong>{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</strong></div>
        </header>

        <div class="info-bar">
            <div>📌 <strong>수집 기간:</strong> {bgn_date_str} | <strong>최소 금액:</strong> <span style="color:#e53e3e; font-weight:bold;">2억 원 이상</span></div>
            <div class="stat-group">
                <div class="stat-card stat-card-bid">🔵 입찰공고 <span>{c_bid}건</span></div>
                <div class="stat-card stat-card-prespec">🟡 사전규격 <span>{c_prespec}건</span></div>
                <div class="stat-card stat-card-orderplan">🟣 발주계획 <span>{c_orderplan}건</span></div>
                <div class="stat-card stat-card-total">🔥 전체 <span id="totalCount">{len(bids)}건</span></div>
            </div>
        </div>

        <div class="controls">
            <input type="text" id="searchInput" class="search-box" placeholder="공고/사업명, 수요기관, 사업책임기술인 검색..." oninput="renderTable()">
            <select id="stageFilter" class="filter-select" onchange="renderTable()">
                <option value="ALL">전체 구분(단계) 보기 (사전규격+발주계획+입찰공고)</option>
                <option value="사전규격">🟡 사전규격공개만 보기</option>
                <option value="발주계획">🟣 발주계획만 보기</option>
                <option value="입찰공고">🔵 입찰공고만 보기</option>
            </select>
            <select id="pmFilter" class="filter-select" onchange="renderTable()">
                <option value="ALL">전체 사업책임기술인 보기</option>
                <option value="STRUCT_PM">🎯 [구조부 주관 가능] 사업책임기술인: 토목구조만 보기</option>
                <option value="OTHER_PM">ℹ️ [타부서 주관] 사업책임기술인: 타분야 보기</option>
            </select>
            <select id="domainFilter" class="filter-select" onchange="renderTable()">
                <option value="ALL">전체 분야 보기</option>
                <option value="기술용역">기술용역</option>
                <option value="공사">공사</option>
            </select>
            <select id="sortSelect" class="filter-select" onchange="renderTable()">
                <option value="PRICE_DESC">추정가격 높은 순</option>
                <option value="PRICE_ASC">추정가격 낮은 순</option>
                <option value="DATE_DESC">최신 일시/기한 순</option>
            </select>
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width: 40px;">#</th>
                    <th style="width: 115px;">구분 (단계)</th>
                    <th style="width: 80px;">분야</th>
                    <th>공고/사업명 & 사업책임기술인(TL) 주관 분야 분석</th>
                    <th style="width: 170px;">수요기관</th>
                    <th style="width: 160px; text-align: right;">추정가격 / 예산</th>
                    <th style="width: 140px; text-align: center;">일시/마감일</th>
                    <th style="width: 85px; text-align: center;">상세보기</th>
                </tr>
            </thead>
            <tbody id="tableBody"></tbody>
        </table>
    </div>

    <script>
        const rawBids = {data_json};

        function formatPrice(valStr) {{
            if (!valStr && valStr !== 0) return "미지정";
            const val = parseInt(valStr, 10);
            if (isNaN(val)) return valStr;
            if (val === 0) return "미지정 (0원)";
            if (val >= 100000000) {{
                return (val / 100000000).toFixed(2) + " 억 원";
            }} else if (val >= 10000) {{
                return Math.floor(val / 10000).toLocaleString() + " 만 원";
            }}
            return val.toLocaleString() + " 원";
        }}

        function renderTable() {{
            const searchKw = document.getElementById('searchInput').value.trim().toLowerCase();
            const stageVal = document.getElementById('stageFilter').value;
            const pmVal = document.getElementById('pmFilter').value;
            const domainVal = document.getElementById('domainFilter').value;
            const sortVal = document.getElementById('sortSelect').value;

            let filtered = rawBids.filter(bid => {{
                const title = (bid._title || '').toLowerCase();
                const instt = (bid._instt || '').toLowerCase();
                const leadField = (bid._lead_field || '').toLowerCase();
                const leadExc = (bid._lead_excerpt || '').toLowerCase();
                const stage = bid._stage || '';
                const domain = bid._domain_kr || '';
                const isStructPm = bid._is_struct_pm || false;

                const matchesSearch = title.includes(searchKw) || instt.includes(searchKw) || leadField.includes(searchKw) || leadExc.includes(searchKw);
                const matchesStage = (stageVal === 'ALL') || (stage === stageVal);
                const matchesDomain = (domainVal === 'ALL') || (domain === domainVal);
                
                let matchesPm = true;
                if (pmVal === 'STRUCT_PM') matchesPm = isStructPm;
                if (pmVal === 'OTHER_PM') matchesPm = !isStructPm;

                return matchesSearch && matchesStage && matchesDomain && matchesPm;
            }});

            filtered.sort((a, b) => {{
                const priceA = parseInt(a._price || 0, 10);
                const priceB = parseInt(b._price || 0, 10);
                if (sortVal === 'PRICE_DESC') return priceB - priceA;
                if (sortVal === 'PRICE_ASC') return priceA - priceB;
                if (sortVal === 'DATE_DESC') return (b._date || '').localeCompare(a._date || '');
                return 0;
            }});

            document.getElementById('totalCount').innerText = filtered.length;

            const tbody = document.getElementById('tableBody');
            if (filtered.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; padding: 40px; color: #a0aec0;">최근 48시간 동안 조건(2억 이상)에 맞는 공고/사전규격/발주계획이 없습니다.</td></tr>`;
                return;
            }}

            tbody.innerHTML = filtered.map((bid, idx) => {{
                const stage = bid._stage || '입찰공고';
                let stageBadgeClass = 'badge-stage-bid';
                let stageLabel = '🔵 입찰공고';
                if (stage === '사전규격') {{ 
                    stageBadgeClass = 'badge-stage-prespec'; 
                    stageLabel = '🟡 사전규격공개'; 
                }} else if (stage === '발주계획') {{ 
                    stageBadgeClass = 'badge-stage-orderplan'; 
                    stageLabel = '🟣 발주계획현황'; 
                }}

                const domainKr = bid._domain_kr || '기타';
                const domainBadgeClass = domainKr === '기술용역' ? 'badge-servc' : 'badge-cnstwk';
                const insttNm = bid._instt || '미지정';
                const priceStr = formatPrice(bid._price);
                const detailUrl = bid._url || '#';
                
                const isStructPm = bid._is_struct_pm || false;
                const leadField = bid._lead_field || '미확인';
                const leadExc = bid._lead_excerpt || '첨부 서류 확인 필요';

                const pmHtml = isStructPm 
                    ? `<div class="pm-badge-struct">🎯 [구조부 주관 가능] 사업책임기술인: <span class="pm-field-name" style="background:#fff; color:#22543d;">${{leadField}}</span> <span style="font-size:11px; font-weight:normal; margin-left:4px;">("${{leadExc}}...")</span></div>`
                    : `<div class="pm-badge-other">ℹ️ [타부서 주관] 사업책임기술인: <span class="pm-field-name" style="background:#fff; color:#2d3748;">${{leadField}}</span> <span style="color:#718096; font-size:11px; margin-left:4px;">("${{leadExc}}...")</span></div>`;

                return `
                    <tr>
                        <td style="text-align: center; color: #718096; font-weight: bold;">${{idx + 1}}</td>
                        <td style="text-align: center;"><span class="badge ${{stageBadgeClass}}">${{stageLabel}}</span></td>
                        <td style="text-align: center;"><span class="badge ${{domainBadgeClass}}">${{domainKr}}</span></td>
                        <td>
                            <a href="${{detailUrl}}" target="_blank" style="color: #2b6cb0; text-decoration: none; font-weight: bold;">${{bid._title}}</a>
                            <div style="color: #718096; font-size: 12px; margin-top: 4px;">공고/계획 ID: ${{bid._id || 'N/A'}}</div>
                            ${{pmHtml}}
                        </td>
                        <td style="color: #4a5568;">${{insttNm}}</td>
                        <td class="price">${{priceStr}}</td>
                        <td class="deadline">${{bid._date || '미지정'}}</td>
                        <td style="text-align: center;">
                            <a href="${{detailUrl}}" target="_blank" class="btn-link">상세보기</a>
                        </td>
                    </tr>
                `;
            }}).join('');
        }}

        document.addEventListener('DOMContentLoaded', renderTable);
    </script>
</body>
</html>"""

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    print("✅ 웹 대시보드(index.html) 2억 이상 수집 완료!")

def main():
    print("🚀 조달청 나라장터 [최근 48시간 교량/다리 2억 이상 (입찰공고·사전규격·발주계획) + 사업책임기술인(TL) 분석] 시작")

    service_key = get_env_or_default('SERVICE_KEY', load_default_service_key())
    bot_token = get_env_or_default('TELEGRAM_BOT_TOKEN')
    chat_id = get_env_or_default('TELEGRAM_CHAT_ID')

    smtp_server = get_env_or_default('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = get_env_or_default('SMTP_PORT', '587')
    smtp_user = get_env_or_default('SMTP_USER')
    smtp_pass = get_env_or_default('SMTP_PASS')
    email_receivers = get_env_or_default('EMAIL_RECEIVERS', 'jh_moon@dohwa.co.kr, moonji8203@gmail.com')

    search_keywords = ['교량', '다리', '교']
    min_price_threshold = 200000000  # 2억 원 이상

    # 48시간 (2일) 기준 조회
    search_days = int(get_env_or_default('SEARCH_DAYS', '2'))

    now = datetime.datetime.now()
    start_date = now - datetime.timedelta(hours=24 * search_days)
    bgn_dt = start_date.strftime('%Y%m%d0000')
    end_dt = now.strftime('%Y%m%d2359')
    bgn_date_str = f"{start_date.strftime('%Y-%m-%d %H:%M')} ~ {now.strftime('%Y-%m-%d %H:%M')}"

    print(f"📌 수집 기간: {bgn_date_str} ({bgn_dt} ~ {end_dt})")
    print(f"💰 최소 금액 조건: {min_price_threshold / 100000000:.0f}억 원 이상")

    all_items_dict = {}

    # 1. 입찰공고 (BidPublicInfoService) 수집 및 1차 필터링
    print("\n🔎 [1/3] 입찰공고 (BidPublicInfoService) 수집 중...")
    for op_name, domain_kr in [("getBidPblancListInfoServcPPSSrch", "기술용역"), ("getBidPblancListInfoCnstwkPPSSrch", "공사")]:
        for kw in search_keywords:
            items = fetch_bid_public(service_key, op_name, kw, bgn_dt, end_dt)
            for item in items:
                title = item.get('bidNtceNm', '').strip()
                presmpt_prce = safe_int(item.get('presmptPrce'))
                asign_bdgt = safe_int(item.get('asignBdgtAmt'))
                price = max(presmpt_prce, asign_bdgt)

                # 2억 이상 조건 및 교량 명칭 필터링
                if price < min_price_threshold:
                    continue
                if not is_target_bridge_title(title):
                    continue

                bid_no = item.get('bidNtceNo')
                key = ('입찰공고', bid_no)
                if bid_no and key not in all_items_dict:
                    item['_stage'] = '입찰공고'
                    item['_domain_kr'] = domain_kr
                    item['_title'] = title
                    item['_price'] = price
                    item['_id'] = bid_no
                    item['_url'] = item.get('bidNtceDtlUrl') or f"https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={bid_no}&bidPbancOrd={item.get('bidNtceOrd', '000')}"
                    item['_instt'] = item.get('dminsttNm') or item.get('ntceInsttNm') or '미지정'
                    item['_date'] = item.get('bidClseDt') or item.get('bidNtceDtm') or ''
                    all_items_dict[key] = item

    # 2. 사전규격공개 (HrcspSsstndrdInfoService) 수집 및 1차 필터링
    print("\n🔎 [2/3] 사전규격공개 (HrcspSsstndrdInfoService) 수집 중...")
    for op_name, domain_kr in [("getPublicPrcureThngInfoServcPPSSrch", "기술용역"), ("getPublicPrcureThngInfoCnstwkPPSSrch", "공사")]:
        items = fetch_pre_spec(service_key, op_name, bgn_dt, end_dt)
        for item in items:
            title = (item.get('prdctClsfcNoNm') or item.get('prprtnNm') or '').strip()
            price = safe_int(item.get('asignBdgtAmt'))

            # 2억 이상 조건 및 교량 명칭 필터링
            if price < min_price_threshold:
                continue
            if not is_target_bridge_title(title):
                continue

            reg_no = item.get('bfSpecRgstNo')
            key = ('사전규격', reg_no)
            if reg_no and key not in all_items_dict:
                item['_stage'] = '사전규격'
                item['_domain_kr'] = domain_kr
                item['_title'] = title
                item['_price'] = price
                item['_id'] = reg_no
                item['_url'] = f"https://www.g2b.go.kr:8081/ep/preparation/preSpecificationDetail.do?bfSpecRgstNo={reg_no}"
                item['_instt'] = item.get('rlDminsttNm') or item.get('orderInsttNm') or '미지정'
                item['_date'] = item.get('opninRgstClseDt') or item.get('rgstDt') or ''
                all_items_dict[key] = item

    # 3. 발주계획 (OrderPlanSttusService) 수집 및 1차 필터링
    print("\n🔎 [3/3] 발주계획 (OrderPlanSttusService) 수집 중...")
    for op_name, domain_kr in [("getOrderPlanSttusListServcPPSSrch", "기술용역"), ("getOrderPlanSttusListCnstwkPPSSrch", "공사")]:
        for kw in search_keywords:
            items = fetch_order_plan(service_key, op_name, kw, bgn_dt, end_dt)
            for item in items:
                title = (item.get('bizNm') or '').strip()
                p1 = safe_int(item.get('orderContrctAmt'))
                p2 = safe_int(item.get('orderThtmContrctAmt'))
                p3 = safe_int(item.get('sumOrderAmt'))
                price = max(p1, p2, p3)

                # 금액이 지정된 경우 2억 이상 필터링 (미지정 0원인 발주계획은 포함)
                if price > 0 and price < min_price_threshold:
                    continue
                if not is_target_bridge_title(title):
                    continue

                plan_no = item.get('orderPlanUntyNo') or item.get('orderPlanSno')
                key = ('발주계획', plan_no)
                if plan_no and key not in all_items_dict:
                    item['_stage'] = '발주계획'
                    item['_domain_kr'] = domain_kr
                    item['_title'] = title
                    item['_price'] = price
                    item['_id'] = plan_no
                    item['_url'] = item.get('orderPlanDtlUrl') or f"https://www.g2b.go.kr/link/PRPA015_01/single/?oderPlanNo={plan_no}"
                    item['_instt'] = item.get('orderInsttNm') or item.get('totlmngInsttNm') or '미지정'
                    date_yr = item.get('orderYear', '')
                    date_mo = item.get('orderMnth', '')
                    item['_date'] = item.get('chgDt') or (f"{date_yr}-{date_mo}" if date_yr and date_mo else '미지정')
                    all_items_dict[key] = item

    all_bids = list(all_items_dict.values())
    all_bids.sort(key=lambda x: safe_int(x.get('_price')), reverse=True)

    print(f"\n🎯 [필터링 완료] 최근 {search_days*24}시간 교량/다리 2억 이상 통과 공고: 총 {len(all_bids)}건 (입찰공고·사전규격·발주계획)")

    # 🔥 [필터링 통과한 대상에 대해서만 사업책임기술인 분석 실행]
    if all_bids:
        print("\n📄 [필터링 통과 대상] 첨부파일(HWP, HWPX, PDF) 파싱하여 사업책임기술인(TL/총괄) 분야 분석 중...")
        for idx, bid in enumerate(all_bids, 1):
            title_disp = bid.get('_title', '')[:30]
            stage_disp = bid.get('_stage', '')
            print(f"   [{idx}/{len(all_bids)}] [{stage_disp}] {title_disp}... 분석 중")
            is_struct_pm, lead_field, lead_exc = analyze_strict_lead_pm(bid)
            bid['_is_struct_pm'] = is_struct_pm
            bid['_lead_field'] = lead_field
            bid['_lead_excerpt'] = lead_exc
            if is_struct_pm:
                print(f"      👉 🎯 [구조부 주관 가능] 사업책임기술인: {lead_field}")
            else:
                print(f"      👉 ℹ️ [타 분야 주관] 사업책임기술인: {lead_field}")

    generate_web_dashboard(all_bids, bgn_date_str)

    tg_messages = build_telegram_messages(all_bids, bgn_date_str)
    if bot_token and chat_id:
        print("\n📤 텔레그램 메시지 발송 중...")
        for msg in tg_messages:
            send_telegram_message(bot_token, chat_id, msg)

    if smtp_user and smtp_pass:
        print("\n✉️ 이메일 리포트 발송 중...")
        send_email_report(smtp_server, smtp_port, smtp_user, smtp_pass, email_receivers, all_bids, bgn_date_str)

if __name__ == "__main__":
    main()
