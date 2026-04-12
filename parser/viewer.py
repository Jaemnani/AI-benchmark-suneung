"""
파싱 완료 후 과목별 HTML 뷰어를 자동 생성합니다.
JSON 데이터를 HTML에 직접 임베드하여 단일 파일로 완성됩니다.
"""
import json
import os


# ─── HTML 템플릿 ─────────────────────────────────────────────────────────────

_HTML_HEAD = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['$','$'],['\\\\(','\\\\)']], displayMath: [['$$','$$']] }},
      options: {{ skipHtmlTags: ['script','noscript','style','textarea','pre'] }}
    }};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" async></script>
  <style>
    *{{box-sizing:border-box}}
    body{{font-family:'Noto Serif KR',serif;max-width:900px;margin:0 auto;padding:24px 16px;background:#f7f7f7;color:#1a1a1a;font-size:15px;line-height:1.6}}
    h1{{font-size:1.25rem;font-weight:700;border-bottom:2px solid #222;padding-bottom:8px;margin-bottom:16px}}
    .meta-info{{font-size:.78rem;color:#666;margin-bottom:16px}}
    /* 탭 */
    .tabs{{display:flex;gap:6px;margin-bottom:20px;flex-wrap:wrap}}
    .tab{{padding:7px 20px;border:1px solid #aaa;border-radius:4px 4px 0 0;cursor:pointer;background:#e8e8e8;font-size:.9rem;font-weight:600;transition:all .15s}}
    .tab.active{{background:#fff;color:#1a56db;border-color:#1a56db;border-bottom-color:#fff}}
    /* 통계 */
    .stats{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}}
    .stat{{background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px 20px;text-align:center;min-width:88px}}
    .stat-num{{font-size:1.5rem;font-weight:700;color:#1a56db}}
    .stat-label{{font-size:.7rem;color:#888;margin-top:2px}}
    /* 섹션 제목 */
    .sec-title{{font-size:.75rem;font-weight:700;letter-spacing:1.5px;color:#555;text-transform:uppercase;margin:26px 0 10px;padding-left:8px;border-left:3px solid #1a56db}}
    /* 문제 카드 */
    .problem{{background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:18px 20px;margin-bottom:14px}}
    .prob-header{{display:flex;align-items:center;gap:10px;margin-bottom:12px}}
    .num{{font-size:1rem;font-weight:700;color:#1a56db;min-width:36px}}
    .badge{{font-size:.68rem;padding:2px 9px;border-radius:10px;font-weight:700}}
    .badge-obj{{background:#e8f0fe;color:#1a56db;border:1px solid #c5d5fb}}
    .badge-short{{background:#fce8e6;color:#c5221f;border:1px solid #f5c6c4}}
    .pts{{font-size:.75rem;color:#888}}
    .question{{line-height:2.1;white-space:pre-wrap;margin-bottom:12px}}
    .bogi{{background:#fafafa;border:1px solid #ddd;border-radius:4px;padding:12px 16px;margin:10px 0;line-height:2.1}}
    .bogi-lbl{{font-size:.72rem;font-weight:700;color:#555;margin-bottom:4px}}
    .jogun{{background:#f5f5f5;border:1px solid #ddd;border-radius:4px;padding:10px 14px;margin:8px 0;line-height:2.1}}
    .img-note{{background:#fffde7;border:1px solid #f0c040;border-radius:4px;padding:7px 12px;margin:8px 0;font-size:.8rem;color:#555}}
    .choices{{list-style:none;padding:0;margin:8px 0 12px;display:grid;grid-template-columns:1fr 1fr;gap:2px 20px}}
    .choices li{{padding:3px 0;line-height:1.9}}
    .ans-wrap{{margin-top:10px;padding-top:8px;border-top:1px solid #f0f0f0}}
    .ans{{display:inline-block;padding:3px 14px;border-radius:4px;font-size:.88rem;font-weight:700}}
    .ans-obj{{background:#e6f4ea;color:#1e7e34;border:1px solid #81c995}}
    .ans-int{{background:#fff3e0;color:#e65100;border:1px solid #ffcc02}}
    .no-data{{color:#aaa;font-style:italic;text-align:center;padding:40px}}
    /* 지문 (국어) */
    .passage{{background:#f9f9f9;border:1px solid #ddd;border-left:4px solid #1a56db;padding:14px 18px;margin:10px 0;border-radius:0 4px 4px 0;line-height:2.1}}
    .passage-lbl{{font-size:.7rem;font-weight:700;color:#1a56db;margin-bottom:6px;text-transform:uppercase;letter-spacing:1px}}
    /* 영어 대본 */
    .script{{background:#f0f7ff;border:1px solid #b3d1f0;border-radius:4px;padding:12px 16px;margin:10px 0;font-style:italic;line-height:1.9;font-size:.9rem}}
    /* 검색 */
    .search-bar{{margin-bottom:16px}}
    .search-bar input{{width:100%;padding:8px 14px;border:1px solid #ccc;border-radius:6px;font-size:.9rem}}
  </style>
</head>
<body>
"""

_HTML_SCRIPT = r"""
<script>
const SUBJECT_TYPE = "$$SUBJECT_TYPE$$";
const TABS = $$TABS$$;
const DATA = $$DATA$$;

function esc(s){if(!s)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function tex(s){return esc(s);}

function switchTab(key){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelector(`.tab[data-key="${key}"]`).classList.add('active');
  render(key);
}

function render(key){
  const el = document.getElementById('content');
  const data = DATA[key];
  if(!data){el.innerHTML='<div class="no-data">데이터 없음</div>';return;}

  let html='';

  if(SUBJECT_TYPE === '수학'){
    html += renderMath(data);
  } else if(SUBJECT_TYPE === '국어'){
    html += renderKorean(data);
  } else {
    html += renderGeneric(data);
  }

  el.innerHTML = html;
  if(window.MathJax) MathJax.typesetPromise([el]);
}

// ── 수학 렌더러 ──────────────────────────────────────────────────────────────
const MATH_SELECTIONS = ['확률과통계','미적분','기하'];
function renderMath(data){
  const meta = data.메타||{};
  const 공통 = data.공통||[];
  const 선택과목 = data.선택과목||{};
  let html = '<div class="stats">';
  html += `<div class="stat"><div class="stat-num">${meta.공통_문제수||공통.length}</div><div class="stat-label">공통</div></div>`;
  MATH_SELECTIONS.forEach(s=>{
    const cnt = (선택과목[s]||[]).length;
    html += `<div class="stat"><div class="stat-num">${cnt}</div><div class="stat-label">${s}</div></div>`;
  });
  html += '</div>';
  html += `<div class="sec-title">공통 (1~22번)</div>`;
  html += 공통.map(renderProblem).join('');
  MATH_SELECTIONS.forEach(s=>{
    const probs = 선택과목[s]||[];
    if(!probs.length) return;
    html += `<div class="sec-title">${s} (23~30번)</div>`;
    html += probs.map(renderProblem).join('');
  });
  return html;
}

// ── 국어 렌더러 ──────────────────────────────────────────────────────────────
function renderKorean(data){
  const probs = data.문제||[];
  let html = `<div class="stats"><div class="stat"><div class="stat-num">${probs.length}</div><div class="stat-label">문제수</div></div></div>`;
  html += probs.map(renderProblem).join('');
  return html;
}

// ── 일반 렌더러 (영어, 한국사, 탐구 등) ────────────────────────────────────
function renderGeneric(data){
  const probs = data.문제||[];
  const meta = data.메타||{};
  let html = `<div class="stats"><div class="stat"><div class="stat-num">${probs.length}</div><div class="stat-label">${meta.과목명||'문제수'}</div></div></div>`;
  html += probs.map(renderProblem).join('');
  return html;
}

// ── 문제 카드 ────────────────────────────────────────────────────────────────
function renderProblem(p){
  const isShort = p.유형 === '단답형';
  let html = `<div class="problem">
    <div class="prob-header">
      <span class="num">${p.번호}번</span>
      <span class="badge ${isShort?'badge-short':'badge-obj'}">${isShort?'단답형':'5지선다'}</span>
      <span class="pts">[${p.배점}점]</span>`;

  if(p.선택과목명) html+=`<span class="pts" style="color:#888">· ${esc(p.선택과목명)}</span>`;
  if(p.유형==='듣기') html+=`<span class="pts" style="color:#2e86c1">🎧 듣기</span>`;
  if(p.시대) html+=`<span class="pts" style="color:#7d6608">· ${esc(p.시대)}</span>`;

  html += `</div><div class="question">${tex(p.문제)}</div>`;

  if(p.지문) html+=`<div class="passage"><div class="passage-lbl">지문</div>${tex(p.지문)}</div>`;
  if(p.대본) html+=`<div class="script">🎧 ${tex(p.대본)}</div>`;
  if(p.보기){
    const items = Array.isArray(p.보기)?p.보기.map(tex).join('<br>'):tex(p.보기);
    html+=`<div class="bogi"><div class="bogi-lbl">&lt;보기&gt;</div>${items}</div>`;
  }
  if(p.조건&&p.조건.length) html+=`<div class="jogun">${p.조건.map(tex).join('<br>')}</div>`;
  if(p.자료&&(p.자료.내용||p.자료.원문)){
    const content = p.자료.내용||p.자료.원문;
    html+=`<div class="bogi"><div class="bogi-lbl">자료 · ${esc(p.자료.유형||'')}</div>${tex(content)}</div>`;
  }
  if(p.has_image&&p.이미지) html+=`<div class="img-note">📷 ${esc(p.이미지.설명)}</div>`;
  if(p.선택지&&p.선택지.length){
    html+=`<ul class="choices">${p.선택지.map(c=>`<li>${tex(c)}</li>`).join('')}</ul>`;
  }
  if(p.정답!==null&&p.정답!==undefined){
    const cls = typeof p.정답==='number'?'ans ans-int':'ans ans-obj';
    html+=`<div class="ans-wrap"><span class="${cls}">정답: ${esc(String(p.정답))}</span></div>`;
  }
  return html+'</div>';
}

// ── 초기화 ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',()=>{
  const first = TABS[0];
  if(first) render(first);
});
</script>
"""

_HTML_TAIL = "</body></html>"


# ─── 과목 유형별 탭 구성 ──────────────────────────────────────────────────────

def _make_tabs_and_data(subject_type: str, output_path: str, subject_config: dict) -> tuple[list[dict], dict]:
    """
    과목 유형에 따라 탭 목록과 데이터 딕셔너리를 반환합니다.
    """
    tabs: list[dict] = []
    data: dict = {}

    if subject_type == "수학":
        # 수학: 홀수형 / 짝수형 탭
        # config["출력"] = OUTPUT_DIR/수학영역.json 이지만
        # 실제 파일은 OUTPUT_DIR/수학영역/홀수형.json 에 저장됨
        output_stem = os.path.splitext(os.path.basename(output_path))[0]  # "수학영역"
        base_dir = os.path.join(os.path.dirname(output_path), output_stem)
        for 형태 in ["홀수형", "짝수형"]:
            path = os.path.join(base_dir, f"{형태}.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data[형태] = json.load(f)
                tabs.append({"key": 형태, "label": 형태})
    else:
        # 단일 과목: 탭 1개
        if os.path.exists(output_path):
            with open(output_path, encoding="utf-8") as f:
                payload = json.load(f)
            key = subject_config.get("과목명", subject_type)
            data[key] = payload
            tabs.append({"key": key, "label": key})

    return tabs, data


def _make_group_tabs_and_data(
    group_subjects: list[tuple[str, dict, str]],
) -> tuple[list[dict], dict]:
    """
    그룹 내 여러 과목의 탭 목록과 데이터를 만듭니다.
    group_subjects: list of (subject_key, subject_config, output_path)
    """
    tabs: list[dict] = []
    data: dict = {}

    for _key, cfg, out_path in group_subjects:
        subject_type = cfg.get("유형", "")
        subject_name = cfg.get("과목명", _key)

        if subject_type == "수학":
            # 수학은 홀수형/짝수형으로 분리 저장됨
            output_stem = os.path.splitext(os.path.basename(out_path))[0]
            base_dir = os.path.join(os.path.dirname(out_path), output_stem)
            for 형태 in ["홀수형", "짝수형"]:
                path = os.path.join(base_dir, f"{형태}.json")
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        payload = json.load(f)
                    key = f"{subject_name}_{형태}"
                    data[key] = payload
                    tabs.append({"key": key, "label": f"{subject_name} {형태}"})
        else:
            if os.path.exists(out_path):
                with open(out_path, encoding="utf-8") as f:
                    payload = json.load(f)
                data[subject_name] = payload
                tabs.append({"key": subject_name, "label": subject_name})

    return tabs, data


def generate_group_viewer(
    group_name: str,
    group_subjects: list[tuple[str, dict, str]],
    output_dir: str,
) -> str | None:
    """
    탐구/제2외국어/직업탐구 등 그룹 전체를 하나의 HTML 뷰어로 생성합니다.
    각 과목이 탭으로 구성되어 같은 문제 번호(1~20 등)가 있어도 과목별로 명확히 구분됩니다.

    Args:
        group_name: 그룹명 (예: '과학탐구영역', '사회탐구영역')
        group_subjects: list of (subject_key, subject_config, output_path)
        output_dir: 그룹 뷰어 파일을 저장할 디렉토리

    Returns:
        생성된 group_viewer.html 경로 또는 None
    """
    tabs, data = _make_group_tabs_and_data(group_subjects)
    if not tabs:
        return None

    # 그룹 내 과목 유형 결정 (첫 번째 과목 기준)
    first_type = group_subjects[0][1].get("유형", "") if group_subjects else ""

    os.makedirs(output_dir, exist_ok=True)
    viewer_path = os.path.join(output_dir, "group_viewer.html")

    연도 = 2026
    title = f"{연도}학년도 대학수학능력시험 — {group_name}"

    # 탭 버튼 HTML
    tab_buttons = "".join(
        f'<button class="tab{" active" if i==0 else ""}" data-key="{t["key"]}" onclick="switchTab(\'{t["key"]}\'">{t["label"]}</button>'
        for i, t in enumerate(tabs)
    )

    data_js = json.dumps(data, ensure_ascii=False)
    tabs_js = json.dumps([t["key"] for t in tabs], ensure_ascii=False)

    script = _HTML_SCRIPT.replace("$$SUBJECT_TYPE$$", first_type)
    script = script.replace("$$TABS$$", tabs_js)
    script = script.replace("$$DATA$$", data_js)

    html = _HTML_HEAD.format(title=title)
    html += f"<h1>{title}</h1>\n"
    html += f'<p class="meta-info">연도: {연도} · 영역: {group_name} · 총 {len(tabs)}개 과목</p>\n'
    html += f'<div class="tabs">{tab_buttons}</div>\n'
    html += '<div id="content"></div>\n'
    html += script
    html += _HTML_TAIL

    with open(viewer_path, "w", encoding="utf-8") as f:
        f.write(html)

    return viewer_path


def generate_viewer(subject_config: dict, output_path: str) -> str | None:
    """
    파싱 완료 후 HTML 뷰어를 생성합니다.

    Args:
        subject_config: SUBJECT_CONFIGS 항목
        output_path: 메인 출력 JSON 경로 (수학은 디렉토리 기준)

    Returns:
        생성된 viewer.html 경로 또는 None
    """
    subject_type = subject_config.get("유형", "")
    subject_name = subject_config.get("과목명", subject_type)
    연도 = 2026

    tabs, data = _make_tabs_and_data(subject_type, output_path, subject_config)
    if not tabs:
        return None

    # viewer.html 저장 위치 결정
    if subject_type == "수학":
        output_stem = os.path.splitext(os.path.basename(output_path))[0]
        viewer_dir = os.path.join(os.path.dirname(output_path), output_stem)
    else:
        viewer_dir = os.path.dirname(output_path)

    os.makedirs(viewer_dir, exist_ok=True)
    viewer_path = os.path.join(viewer_dir, "viewer.html")

    title = f"{연도}학년도 대학수학능력시험 — {subject_name}"

    # 탭 버튼 HTML
    tab_buttons = "".join(
        f'<button class="tab{" active" if i==0 else ""}" data-key="{t["key"]}" onclick="switchTab(\'{t["key"]}\')">{t["label"]}</button>'
        for i, t in enumerate(tabs)
    )

    # 데이터 직렬화 (JSON → JS 리터럴)
    data_js = json.dumps(data, ensure_ascii=False)
    tabs_js = json.dumps([t["key"] for t in tabs], ensure_ascii=False)

    # 스크립트 변수 치환
    script = _HTML_SCRIPT.replace("$$SUBJECT_TYPE$$", subject_type)
    script = script.replace("$$TABS$$", tabs_js)
    script = script.replace("$$DATA$$", data_js)

    html = _HTML_HEAD.format(title=title)
    html += f"<h1>{title}</h1>\n"
    html += f'<p class="meta-info">연도: {연도} · 영역: {subject_type} · 과목: {subject_name}</p>\n'
    html += f'<div class="tabs">{tab_buttons}</div>\n'
    html += '<div id="content"></div>\n'
    html += script
    html += _HTML_TAIL

    with open(viewer_path, "w", encoding="utf-8") as f:
        f.write(html)

    return viewer_path
