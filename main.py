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

BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"

DOMAIN_OPERATIONS = {
    "Servc": ("getBidPblancListInfoServcPPSSrch", "기술용역"),
    "Cnstwk": ("getBidPblancListInfoCnstwkPPSSrch", "공사"),
}

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

def fetch_bids(service_key, op_name, keyword, bgn_dt, end_dt, retries=3):
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

    url = f"{BASE_URL}/{op_name}?{urllib.parse.urlencode(params)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive'
    }

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                header = data.get('response', {}).get('header', {})
                result_code = header.get('resultCode')
                
                if result_code != '00':
                    print(f"⚠️ API Warning [{op_name}]: {header.get('resultMsg')}")
                    return []
                    
                items = data.get('response', {}).get('body', {}).get('items', [])
                if isinstance(items, dict):
                    items = [items]
                return items
        except Exception as e:
            print(f"⚠️ Attempt {attempt}/{retries} failed for [{op_name} - {keyword}]: {e}")
            if attempt < retries:
                time.sleep(3)
    return []

def format_price(amount_str):
    if not amount_str:
        return "미지정"
    try:
        val = int(amount_str)
        if val >= 100000000:
            return f"{val / 100000000:.2f} 억 원 ({val:,} 원)"
        elif val >= 10000:
            return f"{val / 10000:,.0f} 만 원 ({val:,} 원)"
        else:
            return f"{val:,} 원"
    except ValueError:
        return str(amount_str)

def is_target_bridge_title(title):
    # 학교 연계 표현 (예: "초 외 1교", "향교", "중학") 예외 처리
    if re.search(r'외\s*\d+\s*교', title) or '향교' in title or '중학' in title:
        return False
    if '교량' in title or '다리' in title:
        return True
    bridge_pattern = re.compile(r'(?:[가-힣A-Za-z0-9]+교(?=[\s\d_\-\[\(\)\.]|$))')
    matches = bridge_pattern.findall(title)
    false_positives = {'교육', '교체', '교류', '교재', '교환', '교통', '교원', '교실', '교구', '향교', '종교', '설교', '불교', '주교'}
    for m in matches:
        if m not in false_positives:
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

def analyze_structural_engineer(item):
    keywords = ['구조분야', '구조 분야', '토목구조', '토목 구조', '구조책임', '구조 책임', '구조기술사', '구조 기술사', '구조전문', '구조 전문']
    matched_excerpts = []

    for k in range(1, 4):
        doc_url = item.get(f'ntceSpecDocUrl{k}')
        doc_name = item.get(f'ntceSpecFileNm{k}', '')
        if not doc_url or not doc_name:
            continue

        ext = doc_name.split('.')[-1].lower() if '.' in doc_name else ''
        temp_file = f"temp_file_{k}.{ext}"

        if download_attachment(doc_url, temp_file):
            text = ""
            if ext == 'hwpx':
                text = extract_text_hwpx(temp_file)
            elif ext == 'hwp':
                text = extract_text_hwp(temp_file)
            elif ext == 'pdf':
                text = extract_text_pdf(temp_file)

            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

            if text:
                lines = re.split(r'[\.\?\!\n\r]', text)
                for line in lines:
                    line_clean = line.strip()
                    if any(kw in line_clean for kw in keywords):
                        if any(rel in line_clean for rel in ['책임', '기술자', '기술인', '자격', '평가', '배점', '분야', '담당', '투입']):
                            matched_excerpts.append(line_clean[:100])

    if matched_excerpts:
        return True, matched_excerpts[0]
    return False, "첨부파일 서류 직접 확인 필요"

def build_telegram_messages(bids, bgn_date_str):
    header = (
        f"📢 <b>[조달청 나라장터 교량/다리 입찰공고 당일 알림]</b>\n"
        f"📅 조회 기준일: {bgn_date_str}\n"
        f"🔍 검색 분야: 기술용역, 공사\n"
        f"💰 가격 조건: 1억 원 이상\n"
        f"📊 신규 <b>{len(bids)}건</b>의 공고가 등록되었습니다.\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not bids:
        return [header + "오늘 아침 조건에 맞는 신규 입찰 공고가 없습니다. 😊"]

    messages = []
    current_msg = header

    for idx, bid in enumerate(bids, 1):
        title = bid.get('bidNtceNm', '제목 없음').strip()
        bid_no = bid.get('bidNtceNo', 'N/A')
        ord_no = bid.get('bidNtceOrd', '000')
        domain_kr = bid.get('_domain_kr', '기타')
        instt_nm = bid.get('dminsttNm') or bid.get('ntceInsttNm') or '미지정'
        presmpt_prce = format_price(bid.get('presmptPrce'))
        asign_bdgt = format_price(bid.get('asignBdgtAmt'))
        bid_clse_dt = bid.get('bidClseDt', '마감일 미지정')
        cntrct_mthd = bid.get('cntrctCnclsMthdNm', '미지정')
        detail_url = bid.get('bidNtceDtlUrl') or f"https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={bid_no}&bidPbancOrd={ord_no}"

        struct_has = bid.get('_struct_has', False)
        struct_exc = bid.get('_struct_excerpt', '')
        struct_tag = f"<b>✅ 구조분야 확인됨</b>: <i>{struct_exc}</i>" if struct_has else "<b>🔍 구조분야</b>: 첨부 서류 직접 확인 필요"

        item_str = (
            f"<b>{idx}. [{domain_kr}] {title}</b>\n"
            f"• <b>공고번호</b>: {bid_no}\n"
            f"• <b>수요기관</b>: {instt_nm}\n"
            f"• <b>추정가격</b>: {presmpt_prce}\n"
            f"• <b>배정예산</b>: {asign_bdgt}\n"
            f"• <b>계약방법</b>: {cntrct_mthd}\n"
            f"• <b>마감일시</b>: <code>{bid_clse_dt}</code>\n"
            f"• <b>자격분석</b>: {struct_tag}\n"
            f"🔗 <a href='{detail_url}'>나라장터 공고 상세 보기</a>\n\n"
        )

        if len(current_msg) + len(item_str) > 3800:
            messages.append(current_msg)
            current_msg = f"<b>[입찰공고 알림 (이어서)]</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + item_str
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

    subject = f"[조달청 나라장터] 교량/다리/OO교 신규 입찰공고 당일 알림 ({bgn_date_str}) - 총 {len(bids)}건"

    rows_html = ""
    for idx, bid in enumerate(bids, 1):
        title = bid.get('bidNtceNm', '제목 없음').strip()
        bid_no = bid.get('bidNtceNo', 'N/A')
        domain_kr = bid.get('_domain_kr', '기타')
        instt_nm = bid.get('dminsttNm') or bid.get('ntceInsttNm') or '미지정'
        presmpt_prce = format_price(bid.get('presmptPrce'))
        asign_bdgt = format_price(bid.get('asignBdgtAmt'))
        bid_clse_dt = bid.get('bidClseDt', '미지정')
        cntrct_mthd = bid.get('cntrctCnclsMthdNm', '미지정')
        detail_url = bid.get('bidNtceDtlUrl') or f"https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={bid_no}&bidPbancOrd=000"

        struct_has = bid.get('_struct_has', False)
        struct_exc = bid.get('_struct_excerpt', '')
        
        if struct_has:
            struct_badge = f'<div style="margin-top:6px; background-color:#e6fffa; color:#234e52; border:1px solid #b2f5ea; padding:4px 8px; border-radius:4px; font-size:12px;"><strong>✅ 구조분야 기술인 발견:</strong> {struct_exc}</div>'
        else:
            struct_badge = f'<div style="margin-top:6px; color:#718096; font-size:11px;">🔍 구조분야 자격: 첨부 서류 직접 확인 필요</div>'

        bg_color = "#ffffff" if idx % 2 != 0 else "#f9fbfd"

        rows_html += f"""
        <tr style="background-color: {bg_color}; border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 12px; text-align: center; font-weight: bold; color: #4a5568;">{idx}</td>
            <td style="padding: 12px; text-align: center;">
                <span style="background-color: #ebf8ff; color: #2b6cb0; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{domain_kr}</span>
            </td>
            <td style="padding: 12px;">
                <a href="{detail_url}" target="_blank" style="color: #2b6cb0; text-decoration: none; font-weight: bold;">{title}</a>
                <div style="color: #718096; font-size: 12px; margin-top: 4px;">공고번호: {bid_no} | 계약방식: {cntrct_mthd}</div>
                {struct_badge}
            </td>
            <td style="padding: 12px; color: #2d3748; font-weight: 500;">{instt_nm}</td>
            <td style="padding: 12px; text-align: right; color: #2c5282; font-weight: bold;">{presmpt_prce}<br><span style="color:#718096; font-size:11px; font-weight:normal;">(예산: {asign_bdgt})</span></td>
            <td style="padding: 12px; text-align: center; color: #e53e3e; font-size: 13px; font-family: monospace;">{bid_clse_dt}</td>
            <td style="padding: 12px; text-align: center;">
                <a href="{detail_url}" target="_blank" style="background-color: #3182ce; color: #ffffff; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-size: 12px; display: inline-block;">공고보기</a>
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
        <div style="max-width: 1050px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
            <h2 style="color: #1a365d; margin-top: 0; border-bottom: 2px solid #3182ce; padding-bottom: 12px;">
                🌉 조달청 나라장터 입찰공고 당일 리포트
            </h2>
            <div style="background-color: #ebf8ff; border-left: 4px solid #3182ce; padding: 12px 16px; margin-bottom: 24px; border-radius: 4px; color: #2c5282;">
                <strong>📅 조회 기준일:</strong> {bgn_date_str} | 
                <strong>🔍 대상 분야:</strong> 기술용역, 공사 | 
                <strong>💰 최소 금액:</strong> 1억 원 이상 | 
                <strong>📊 당일 수집 건수:</strong> <span style="font-size:18px; font-weight:bold; color:#e53e3e;">{len(bids)}건</span>
            </div>

            <table style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px;">
                <thead>
                    <tr style="background-color: #2b6cb0; color: #ffffff;">
                        <th style="padding: 12px; width: 40px;">#</th>
                        <th style="padding: 12px; width: 80px;">분야</th>
                        <th style="padding: 12px;">입찰 공고명 & 자격 분석</th>
                        <th style="padding: 12px; width: 140px;">수요기관</th>
                        <th style="padding: 12px; width: 150px; text-align: right;">추정가격 / 예산</th>
                        <th style="padding: 12px; width: 140px; text-align: center;">입찰마감일시</th>
                        <th style="padding: 12px; width: 90px; text-align: center;">상세링크</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html if bids else '<tr><td colspan="7" style="padding: 30px; text-align: center; color: #a0aec0;">오늘 조건에 맞는 신규 입찰 공고가 없습니다.</td></tr>'}
                </tbody>
            </table>

            <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #e2e8f0; text-color: #a0aec0; font-size: 12px; text-align: center;">
                본 이메일은 GitHub Actions 자동화 시스템에 의해 수신인({', '.join(receiver_list)})에게 매일 아침 발송됩니다.
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
    
    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>조달청 나라장터 교량/공사 입찰 대시보드</title>
    <style>
        * {{ box-sizing: border-box; font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif; }}
        body {{ background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
        .container {{ max-width: 1250px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
        header {{ border-bottom: 2px solid #2b6cb0; padding-bottom: 15px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }}
        h1 {{ color: #1a365d; margin: 0; font-size: 24px; }}
        .info-bar {{ background: #ebf8ff; color: #2c5282; padding: 12px 18px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; font-weight: 500; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
        
        .controls {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
        .search-box {{ flex: 1; min-width: 250px; padding: 10px 16px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 14px; outline: none; }}
        .filter-select {{ padding: 10px 14px; border: 1px solid #cbd5e0; border-radius: 6px; font-size: 14px; background: #fff; cursor: pointer; }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
        th {{ background: #2b6cb0; color: #fff; padding: 12px; text-align: left; font-weight: 600; }}
        td {{ padding: 12px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }}
        tr:hover {{ background-color: #f7fafc; }}

        .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; text-align: center; }}
        .badge-servc {{ background-color: #ebf8ff; color: #2b6cb0; }}
        .badge-cnstwk {{ background-color: #feebc8; color: #c05621; }}
        
        .struct-badge-yes {{ margin-top: 6px; background-color: #e6fffa; color: #234e52; border: 1px solid #b2f5ea; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }}
        .struct-badge-no {{ margin-top: 6px; color: #a0aec0; font-size: 11px; }}

        .price {{ color: #2c5282; font-weight: bold; text-align: right; }}
        .deadline {{ color: #e53e3e; font-size: 13px; font-family: monospace; text-align: center; }}
        .btn-link {{ background-color: #3182ce; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: 500; display: inline-block; transition: background 0.2s; }}
        .btn-link:hover {{ background-color: #2b6cb0; }}
        .count-tag {{ background: #e53e3e; color: white; padding: 2px 8px; border-radius: 12px; font-weight: bold; margin-left: 6px; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🌉 조달청 나라장터 입찰공고 대시보드 (당일 기준)</h1>
            <div style="font-size: 13px; color: #718096;">최종 업데이트: <strong>{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</strong></div>
        </header>

        <div class="info-bar">
            <div>📌 <strong>조회 기준:</strong> 당일 ({bgn_date_str}) | <strong>대상 분야:</strong> 기술용역, 공사 | <strong>조건:</strong> 1억 원 이상</div>
            <div>수집된 공고: <span class="count-tag" id="totalCount">{len(bids)}</span>건</div>
        </div>

        <div class="controls">
            <input type="text" id="searchInput" class="search-box" placeholder="공고명, 수요기관, 자격 문구 검색..." oninput="renderTable()">
            <select id="structFilter" class="filter-select" onchange="renderTable()">
                <option value="ALL">전체 자격 조건 보기</option>
                <option value="STRUCT_ONLY">구조분야 기술인 명시 공고만 보기 (✅)</option>
            </select>
            <select id="domainFilter" class="filter-select" onchange="renderTable()">
                <option value="ALL">전체 분야 보기</option>
                <option value="기술용역">기술용역</option>
                <option value="공사">공사</option>
            </select>
            <select id="sortSelect" class="filter-select" onchange="renderTable()">
                <option value="PRICE_DESC">추정가격 높은 순</option>
                <option value="PRICE_ASC">추정가격 낮은 순</option>
                <option value="DATE_DESC">최신 게시일 순</option>
            </select>
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width: 50px;">#</th>
                    <th style="width: 90px;">분야</th>
                    <th>입찰 공고명 & 첨부파일 기술인 분석</th>
                    <th style="width: 170px;">수요기관</th>
                    <th style="width: 170px; text-align: right;">추정가격 (배정예산)</th>
                    <th style="width: 150px; text-align: center;">입찰마감일시</th>
                    <th style="width: 90px; text-align: center;">상세보기</th>
                </tr>
            </thead>
            <tbody id="tableBody"></tbody>
        </table>
    </div>

    <script>
        const rawBids = {data_json};

        function formatPrice(valStr) {{
            if (!valStr) return "미지정";
            const val = parseInt(valStr, 10);
            if (isNaN(val)) return valStr;
            if (val >= 100000000) {{
                return (val / 100000000).toFixed(2) + " 억 원";
            }} else if (val >= 10000) {{
                return Math.floor(val / 10000).toLocaleString() + " 만 원";
            }}
            return val.toLocaleString() + " 원";
        }}

        function renderTable() {{
            const searchKw = document.getElementById('searchInput').value.trim().toLowerCase();
            const structVal = document.getElementById('structFilter').value;
            const domainVal = document.getElementById('domainFilter').value;
            const sortVal = document.getElementById('sortSelect').value;

            let filtered = rawBids.filter(bid => {{
                const title = (bid.bidNtceNm || '').toLowerCase();
                const instt = (bid.dminsttNm || bid.ntceInsttNm || '').toLowerCase();
                const structExc = (bid._struct_excerpt || '').toLowerCase();
                const domain = bid._domain_kr || '';
                const hasStruct = bid._struct_has || false;

                const matchesSearch = title.includes(searchKw) || instt.includes(searchKw) || structExc.includes(searchKw);
                const matchesDomain = (domainVal === 'ALL') || (domain === domainVal);
                const matchesStruct = (structVal === 'ALL') || (structVal === 'STRUCT_ONLY' && hasStruct);

                return matchesSearch && matchesDomain && matchesStruct;
            }});

            filtered.sort((a, b) => {{
                const priceA = parseInt(a.presmptPrce || a.asignBdgtAmt || 0, 10);
                const priceB = parseInt(b.presmptPrce || b.asignBdgtAmt || 0, 10);
                if (sortVal === 'PRICE_DESC') return priceB - priceA;
                if (sortVal === 'PRICE_ASC') return priceA - priceB;
                if (sortVal === 'DATE_DESC') return (b.bidNtceDt || '').localeCompare(a.bidNtceDt || '');
                return 0;
            }});

            document.getElementById('totalCount').innerText = filtered.length;

            const tbody = document.getElementById('tableBody');
            if (filtered.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 40px; color: #a0aec0;">당일 조건에 맞는 신규 입찰 공고가 없습니다.</td></tr>`;
                return;
            }}

            tbody.innerHTML = filtered.map((bid, idx) => {{
                const domainKr = bid._domain_kr || '기타';
                const badgeClass = domainKr === '기술용역' ? 'badge-servc' : 'badge-cnstwk';
                const insttNm = bid.dminsttNm || bid.ntceInsttNm || '미지정';
                const priceStr = formatPrice(bid.presmptPrce || bid.asignBdgtAmt);
                const detailUrl = bid.bidNtceDtlUrl || `https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=${{bid.bidNtceNo}}&bidPbancOrd=${{bid.bidNtceOrd || '000'}}`;
                
                const hasStruct = bid._struct_has || false;
                const structExc = bid._struct_excerpt || '';

                const structHtml = hasStruct 
                    ? `<div class="struct-badge-yes"><strong>✅ 구조분야 기술인 요구:</strong> "${{structExc}}..."</div>`
                    : `<div class="struct-badge-no">🔍 구조분야 자격: 첨부 서류 직접 확인 필요</div>`;

                return `
                    <tr>
                        <td style="text-align: center; color: #718096; font-weight: bold;">${{idx + 1}}</td>
                        <td style="text-align: center;"><span class="badge ${{badgeClass}}">${{domainKr}}</span></td>
                        <td>
                            <a href="${{detailUrl}}" target="_blank" style="color: #2b6cb0; text-decoration: none; font-weight: bold;">${{bid.bidNtceNm}}</a>
                            <div style="color: #718096; font-size: 12px; margin-top: 4px;">공고번호: ${{bid.bidNtceNo}} | 계약방법: ${{bid.cntrctCnclsMthdNm || '미지정'}}</div>
                            ${{structHtml}}
                        </td>
                        <td style="color: #4a5568;">${{insttNm}}</td>
                        <td class="price">${{priceStr}}</td>
                        <td class="deadline">${{bid.bidClseDt || '미지정'}}</td>
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
    print("✅ 웹 대시보드(index.html) 당일 기준 생성 완료!")

def main():
    print("🚀 조달청 나라장터 [당일 기준 교량/공사 1억 이상 + 첨부파일 구조기술자 분석] 시작")

    service_key = get_env_or_default('SERVICE_KEY', load_default_service_key())
    bot_token = get_env_or_default('TELEGRAM_BOT_TOKEN')
    chat_id = get_env_or_default('TELEGRAM_CHAT_ID')

    smtp_server = get_env_or_default('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = get_env_or_default('SMTP_PORT', '587')
    smtp_user = get_env_or_default('SMTP_USER')
    smtp_pass = get_env_or_default('SMTP_PASS')
    email_receivers = get_env_or_default('EMAIL_RECEIVERS', 'jh_moon@dohwa.co.kr, moonji8203@gmail.com')

    target_domains = ['Servc', 'Cnstwk']
    search_keywords = ['교량', '다리', '교']
    exclude_keywords = ['학교', '초등', '고등']
    min_price_threshold = 100000000

    # 당일 기준 조회 (매일 아침 실행되므로 1일 전~당일 24시간 범위)
    search_days = int(get_env_or_default('SEARCH_DAYS', '1'))

    now = datetime.datetime.now()
    start_date = now - datetime.timedelta(days=search_days)
    bgn_dt = start_date.strftime('%Y%m%d0000')
    end_dt = now.strftime('%Y%m%d2359')
    bgn_date_str = f"{start_date.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}"

    print(f"📌 당일 검색 기간: {bgn_dt} ~ {end_dt}")

    all_bids_dict = {}

    for domain in target_domains:
        op_name, domain_kr = DOMAIN_OPERATIONS[domain]
        print(f"\n🔎 [{domain_kr}] 분야 당일 수집 중...")

        for kw in search_keywords:
            items = fetch_bids(service_key, op_name, kw, bgn_dt, end_dt)
            print(f"   - 키워드 '{kw}': {len(items)}건 수집")

            for item in items:
                bid_no = item.get('bidNtceNo')
                title = item.get('bidNtceNm', '').strip()
                presmpt_prce = int(item.get('presmptPrce') or 0)
                asign_bdgt = int(item.get('asignBdgtAmt') or 0)
                price = max(presmpt_prce, asign_bdgt)

                if any(ex in title for ex in exclude_keywords):
                    continue

                if price < min_price_threshold:
                    continue

                if not is_target_bridge_title(title):
                    continue

                if bid_no and bid_no not in all_bids_dict:
                    item['_domain_kr'] = domain_kr
                    all_bids_dict[bid_no] = item

    all_bids = list(all_bids_dict.values())
    all_bids.sort(key=lambda x: int(x.get('presmptPrce') or x.get('asignBdgtAmt') or 0), reverse=True)

    print(f"\n🎯 당일 정제된 교량/공사 대상 공고 수: {len(all_bids)}건")

    if all_bids:
        print("📄 당일 공고 첨부파일(HWP, HWPX, PDF) 다운로드 및 구조분야 책임기술인 요건 자동 분석 중...")
        for idx, bid in enumerate(all_bids, 1):
            print(f"   [{idx}/{len(all_bids)}] {bid.get('bidNtceNm')[:30]}... 분석 중")
            has_struct, excerpt = analyze_structural_engineer(bid)
            bid['_struct_has'] = has_struct
            bid['_struct_excerpt'] = excerpt
            if has_struct:
                print(f"      👉 ✅ 구조분야 자격 명시 확인됨: {excerpt[:50]}")

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
