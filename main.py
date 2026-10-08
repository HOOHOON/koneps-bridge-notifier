import os
import sys
import json
import re
import datetime
import urllib.parse
import urllib.request
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# UTF-8 출력 설정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# API 기본 EndPoint
BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"

# 분야별 오퍼레이션 매핑
DOMAIN_OPERATIONS = {
    "Servc": ("getBidPblancListInfoServcPPSSrch", "기술용역"),
    "Cnstwk": ("getBidPblancListInfoCnstwkPPSSrch", "공사"),
}

def load_default_service_key():
    """로컬 txt 파일에서 API 키를 읽어오는 헬퍼 함수"""
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

def fetch_bids(service_key, op_name, keyword, bgn_dt, end_dt):
    """나라장터 Open API 호출"""
    params = {
        'serviceKey': service_key,
        'type': 'json',
        'inqryDiv': '1',  # 1: 공고게시일시 기준
        'inqryBgnDt': bgn_dt,
        'inqryEndDt': end_dt,
        'numOfRows': '100',
        'pageNo': '1'
    }
    if keyword:
        params['bidNtceNm'] = keyword

    url = f"{BASE_URL}/{op_name}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            header = data.get('response', {}).get('header', {})
            result_code = header.get('resultCode')
            
            if result_code != '00':
                print(f"API Warning [{op_name}]: {header.get('resultMsg')}")
                return []
                
            items = data.get('response', {}).get('body', {}).get('items', [])
            if isinstance(items, dict):
                items = [items]
            return items
    except Exception as e:
        print(f"API Request Error [{op_name} - {keyword}]: {e}")
        return []

def format_price(amount_str):
    """금액 포맷팅 (원 -> 억/만원 단위 변환)"""
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
    """
    '교량', '다리', 'OO교' (예: 학산교, 태봉2교, 삼산교 등) 단어 정규식 감지.
    단, '교육', '교체', '교류' 등의 오탐 방지 로직 포함.
    """
    if '교량' in title or '다리' in title:
        return True
    # ~교 패턴 (단어 끝이 '교'로 끝나거나 뒤에 공백/숫자/기호가 오는 한글/숫자 조합)
    # 예: 학산교, 관춘1교, 신흥교 등
    bridge_pattern = re.compile(r'(?:[가-힣A-Za-z0-9]+교(?=[\s\d_\-\[\(\)\.]|$))')
    matches = bridge_pattern.findall(title)
    
    # '교육', '교체', '교류', '교재', '교환', '교통' 등 오탐 단어 필터링
    false_positives = {'교육', '교체', '교류', '교재', '교환', '교통', '교원', '교실', '교구'}
    for m in matches:
        if m not in false_positives:
            return True
    return False

def build_telegram_messages(bids, bgn_date_str):
    """텔레그램용 HTML 메시지 리스트 생성"""
    header = (
        f"📢 <b>[조달청 나라장터 교량/다리 입찰공고 알림]</b>\n"
        f"📅 조회 기간: {bgn_date_str}\n"
        f"🔍 검색 분야: 기술용역, 공사\n"
        f"💰 가격 조건: 1억 원 이상\n"
        f"📊 총 <b>{len(bids)}건</b>의 맞춤 공고가 발견되었습니다.\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not bids:
        return [header + "오늘 조건에 맞는 신규 입찰 공고가 없습니다. 😊"]

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

        item_str = (
            f"<b>{idx}. [{domain_kr}] {title}</b>\n"
            f"• <b>공고번호</b>: {bid_no}\n"
            f"• <b>수요기관</b>: {instt_nm}\n"
            f"• <b>추정가격</b>: {presmpt_prce}\n"
            f"• <b>배정예산</b>: {asign_bdgt}\n"
            f"• <b>계약방법</b>: {cntrct_mthd}\n"
            f"• <b>마감일시</b>: <code>{bid_clse_dt}</code>\n"
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
    """텔레그램 API로 메시지 전송"""
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
    """이메일(HTML 서식 리포트) 전송"""
    if not smtp_user or not smtp_pass or not receivers:
        print("⚠️ SMTP 이메일 설정이 부재하여 이메일 발송을 건너땁니다.")
        return False

    receiver_list = [r.strip() for r in receivers.split(',') if r.strip()]
    if not receiver_list:
        return False

    subject = f"[조달청 나라장터] 교량/다리/OO교 신규 입찰공고 알림 ({bgn_date_str}) - 총 {len(bids)}건"

    # HTML 이메일 본문 생성
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
        <div style="max-width: 1000px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
            <h2 style="color: #1a365d; margin-top: 0; border-bottom: 2px solid #3182ce; padding-bottom: 12px;">
                🌉 조달청 나라장터 입찰공고 매일 리포트
            </h2>
            <div style="background-color: #ebf8ff; border-left: 4px solid #3182ce; padding: 12px 16px; margin-bottom: 24px; border-radius: 4px; color: #2c5282;">
                <strong>📅 조회 기준일:</strong> {bgn_date_str} | 
                <strong>🔍 대상 분야:</strong> 기술용역, 공사 | 
                <strong>💰 최소 금액:</strong> 1억 원 이상 | 
                <strong>📊 총 수집 건수:</strong> <span style="font-size:18px; font-weight:bold; color:#e53e3e;">{len(bids)}건</span>
            </div>

            <table style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px;">
                <thead>
                    <tr style="background-color: #2b6cb0; color: #ffffff;">
                        <th style="padding: 12px; width: 40px;">#</th>
                        <th style="padding: 12px; width: 80px;">분야</th>
                        <th style="padding: 12px;">입찰 공고명</th>
                        <th style="padding: 12px; width: 140px;">수요기관</th>
                        <th style="padding: 12px; width: 160px; text-align: right;">추정가격 / 예산</th>
                        <th style="padding: 12px; width: 140px; text-align: center;">입찰마감일시</th>
                        <th style="padding: 12px; width: 90px; text-align: center;">상세링크</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html if bids else '<tr><td colspan="7" style="padding: 30px; text-align: center; color: #a0aec0;">조건에 맞는 신규 입찰 공고가 없습니다.</td></tr>'}
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
    """GitHub Pages용 인터랙티브 웹 대시보드(index.html) 생성"""
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
        .container {{ max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
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
            <h1>🌉 조달청 나라장터 입찰공고 대시보드</h1>
            <div style="font-size: 13px; color: #718096;">최종 업데이트: <strong>{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</strong></div>
        </header>

        <div class="info-bar">
            <div>📌 <strong>조회 기간:</strong> {bgn_date_str} | <strong>대상 분야:</strong> 기술용역, 공사 | <strong>조건:</strong> 추정가격 1억 원 이상</div>
            <div>수집된 공고: <span class="count-tag" id="totalCount">{len(bids)}</span>건</div>
        </div>

        <div class="controls">
            <input type="text" id="searchInput" class="search-box" placeholder="공고명, 수요기관, 공고번호 검색..." oninput="renderTable()">
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
                    <th>입찰 공고명</th>
                    <th style="width: 180px;">수요기관</th>
                    <th style="width: 180px; text-align: right;">추정가격 (배정예산)</th>
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
            const domainVal = document.getElementById('domainFilter').value;
            const sortVal = document.getElementById('sortSelect').value;

            let filtered = rawBids.filter(bid => {{
                const title = (bid.bidNtceNm || '').toLowerCase();
                const instt = (bid.dminsttNm || bid.ntceInsttNm || '').toLowerCase();
                const bidNo = (bid.bidNtceNo || '').toLowerCase();
                const domain = bid._domain_kr || '';

                const matchesSearch = title.includes(searchKw) || instt.includes(searchKw) || bidNo.includes(searchKw);
                const matchesDomain = (domainVal === 'ALL') || (domain === domainVal);

                return matchesSearch && matchesDomain;
            }});

            // 정렬 로직
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
                tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; padding: 40px; color: #a0aec0;">검색 결과에 맞는 입찰 공고가 없습니다.</td></tr>`;
                return;
            }}

            tbody.innerHTML = filtered.map((bid, idx) => {{
                const domainKr = bid._domain_kr || '기타';
                const badgeClass = domainKr === '기술용역' ? 'badge-servc' : 'badge-cnstwk';
                const insttNm = bid.dminsttNm || bid.ntceInsttNm || '미지정';
                const priceStr = formatPrice(bid.presmptPrce || bid.asignBdgtAmt);
                const detailUrl = bid.bidNtceDtlUrl || `https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=${{bid.bidNtceNo}}&bidPbancOrd=${{bid.bidNtceOrd || '000'}}`;

                return `
                    <tr>
                        <td style="text-align: center; color: #718096; font-weight: bold;">${{idx + 1}}</td>
                        <td style="text-align: center;"><span class="badge ${{badgeClass}}">${{domainKr}}</span></td>
                        <td>
                            <a href="${{detailUrl}}" target="_blank" style="color: #2b6cb0; text-decoration: none; font-weight: bold;">${{bid.bidNtceNm}}</a>
                            <div style="color: #718096; font-size: 12px; margin-top: 4px;">공고번호: ${{bid.bidNtceNo}} | 계약방법: ${{bid.cntrctCnclsMthdNm || '미지정'}}</div>
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
    print("✅ 웹 대시보드(index.html) 생성 완료!")

def main():
    print("🚀 조달청 나라장터 [교량/공사 1억 이상] 자동 알림 스크립트 시작")

    # 1. 환경 변수 및 기본 설정
    service_key = get_env_or_default('SERVICE_KEY', load_default_service_key())
    bot_token = get_env_or_default('TELEGRAM_BOT_TOKEN')
    chat_id = get_env_or_default('TELEGRAM_CHAT_ID')

    # 이메일 관련 환경 변수 (기본값 설정)
    smtp_server = get_env_or_default('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = get_env_or_default('SMTP_PORT', '587')
    smtp_user = get_env_or_default('SMTP_USER')
    smtp_pass = get_env_or_default('SMTP_PASS')
    email_receivers = get_env_or_default('EMAIL_RECEIVERS', 'jh_moon@dohwa.co.kr, moonji8203@gmail.com')

    # 필터 조건 설정
    target_domains = ['Servc', 'Cnstwk']  # 기술용역, 공사
    search_keywords = ['교량', '다리', '교']  # API 호출용 키워드
    exclude_keywords = ['학교', '초등', '고등']  # 제외 키워드
    min_price_threshold = 100000000  # 1억 원 이상

    search_days = int(get_env_or_default('SEARCH_DAYS', '2'))  # 기본 최근 2일 조회

    # 2. 조회 기간 계산
    now = datetime.datetime.now()
    start_date = now - datetime.timedelta(days=search_days)
    bgn_dt = start_date.strftime('%Y%m%d0000')
    end_dt = now.strftime('%Y%m%d2359')
    bgn_date_str = f"{start_date.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}"

    print(f"📌 검색 기간: {bgn_dt} ~ {end_dt}")
    print(f"📌 검색 분야: 기술용역(Servc), 공사(Cnstwk)")
    print(f"📌 키워드 조건: '교량', '다리', 'OO교' 패턴 감지 (제외: {exclude_keywords})")
    print(f"📌 가격 조건: 추정가격/예산 {min_price_threshold:,} 원 이상")

    # 3. 공고 수집 및 정교한 필터링
    all_bids_dict = {}

    for domain in target_domains:
        op_name, domain_kr = DOMAIN_OPERATIONS[domain]
        print(f"\n🔎 [{domain_kr}] 분야 수집 중...")

        for kw in search_keywords:
            items = fetch_bids(service_key, op_name, kw, bgn_dt, end_dt)
            print(f"   - API 검색어 '{kw}': {len(items)}건 응답")

            for item in items:
                bid_no = item.get('bidNtceNo')
                title = item.get('bidNtceNm', '').strip()
                presmpt_prce = int(item.get('presmptPrce') or 0)
                asign_bdgt = int(item.get('asignBdgtAmt') or 0)
                price = max(presmpt_prce, asign_bdgt)

                # 조건 1: 제외 키워드 검사 ('학교', '초등', '고등')
                if any(ex in title for ex in exclude_keywords):
                    continue

                # 조건 2: 가격 1억 원 이상 검사
                if price < min_price_threshold:
                    continue

                # 조건 3: '교량', '다리', 'OO교' 패턴 검사
                if not is_target_bridge_title(title):
                    continue

                if bid_no and bid_no not in all_bids_dict:
                    item['_domain_kr'] = domain_kr
                    all_bids_dict[bid_no] = item

    all_bids = list(all_bids_dict.values())
    all_bids.sort(key=lambda x: int(x.get('presmptPrce') or x.get('asignBdgtAmt') or 0), reverse=True)

    print(f"\n🎯 최종 필터링 완료된 공고 수: {len(all_bids)}건")

    # 4. 웹 대시보드(index.html) 생성
    generate_web_dashboard(all_bids, bgn_date_str)

    # 5. 텔레그램 알림 발송
    tg_messages = build_telegram_messages(all_bids, bgn_date_str)
    if bot_token and chat_id:
        print("\n📤 텔레그램 메시지 발송 중...")
        for msg in tg_messages:
            send_telegram_message(bot_token, chat_id, msg)
    else:
        print("\n⚠️ 텔레그램 토큰 미설정 (콘솔 출력)")
        print(tg_messages[0][:500])

    # 6. 이메일 발송
    if smtp_user and smtp_pass:
        print("\n✉️ 이메일 리포트 발송 중...")
        send_email_report(smtp_server, smtp_port, smtp_user, smtp_pass, email_receivers, all_bids, bgn_date_str)
    else:
        print("\n💡 SMTP_USER / SMTP_PASS 환경 변수를 등록하면 지정하신 이메일로 매일 리포트가 발송됩니다.")

if __name__ == "__main__":
    main()
