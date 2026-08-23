/* 상승 섹터 포착 — 웹앱 (132차)
 *
 * 이 파일은 **보여 주기만** 합니다. 계산·판정은 전부 로봇이 미리 해서
 * docs/data/app.json 에 넣어 둔 것을 읽습니다 (설계도 3장의 경계).
 * 없는 값은 만들지 않고 "없음"이라고 적습니다 (헌법 1조).
 */

const DATA = "data/app.json";
const TICK = (t) => `data/t/${t}.json`;

let APP = null;                 // app.json 내용
const 종목캐시 = {};             // 종목 상세 (한 번 받으면 다시 안 받음)

/* ── 도우미 ──────────────────────────────────────────── */
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** 계기판 설명문에 쓰인 **굵게** 표시를 살려 줍니다 (글자 그대로 보이지 않게).
 *  반드시 esc() 로 막은 **뒤에** 굵게만 되살립니다 — 남의 글자를 코드로
 *  실행시키지 않기 위해서입니다. */
const bold = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");

/** 숫자를 화면용 글자로 — 없으면 "—" (지어내지 않음) */
function num(v, digits = 1, sign = false) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Number(v).toFixed(digits);
  return sign && v > 0 ? `+${s}` : s;
}

/** 꺾은선 차트 (SVG 직접 그리기 — 외부 라이브러리 없음) */
function sparkline(points, color, height = 96) {
  const vals = points.map((p) => p[1]).filter((v) => v !== null);
  if (vals.length < 2) return '<div class="empty">그릴 자료가 모자랍니다.</div>';
  const W = 340, H = height, pad = 4;
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const step = (W - pad * 2) / (points.length - 1);
  let d = "";
  points.forEach((p, i) => {
    if (p[1] === null) return;
    const x = pad + i * step;
    const y = pad + (H - pad * 2) * (1 - (p[1] - lo) / span);
    d += (d ? " L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

/** 두 줄 겹쳐 그리기 (주가 + 52주선) */
function twoLines(주봉, ma, height = 150) {
  const vals = 주봉.map((p) => p[1]).concat(ma.filter((v) => v !== null));
  if (vals.length < 2) return '<div class="empty">그릴 자료가 모자랍니다.</div>';
  const W = 340, H = height, pad = 4;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const step = (W - pad * 2) / (주봉.length - 1);
  const path = (arr, get) => {
    let d = "";
    arr.forEach((p, i) => {
      const v = get(p, i);
      if (v === null || v === undefined) return;
      const x = pad + i * step;
      const y = pad + (H - pad * 2) * (1 - (v - lo) / span);
      d += (d ? " L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    });
    return d;
  };
  const p1 = path(주봉, (p) => p[1]);
  const p2 = path(주봉, (_p, i) => ma[i]);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path d="${p2}" fill="none" stroke="#93a1ad" stroke-width="1.4"
      stroke-dasharray="4 3"/>
    <path d="${p1}" fill="none" stroke="#00c805" stroke-width="2"
      stroke-linejoin="round"/></svg>`;
}

/** 델타 흐름 막대 (150차-L · 150차-M 에 성장률 추가)
 *
 * 막대 **높이** = 그 분기의 잣대 값. 색은 **세 단계**입니다:
 *   진초록 = 값이 올랐고 **성장이 빨라짐**(가속)
 *   연초록 = 값은 올랐지만 **성장이 느려짐**(둔화)
 *   빨강   = 값이 내려감
 *
 * 왜 세 단계인가 — 주인 지적(150차-M): "1-10-20 이면 올라갔다 내려가야지.
 * 10배에서 2배가 된 거니까." 방향만 보면 셋 다 "상승"이지만 성장은
 * **+900% → +100%** 로 꺾였습니다. 실물 CRDO 도 값은 계속 오르는데
 * 성장률이 257% → 168% → 60% → 8% 로 뚜렷이 둔화 중입니다.
 *
 * 값을 못 잰 분기는 **그리지 않습니다** — 0 으로 그리면 "이익이 0"으로
 * 읽혀 거짓이 됩니다(헌법 1조). 값이 0 이하를 지나 성장률의 뜻이
 * 뒤집히는 칸은 가속·둔화를 **판단하지 않고** 중간색으로 둡니다.
 */
function 델타막대(흐름, height = 100) {
  const 있는 = 흐름.filter((x) => x.값 !== null && x.값 !== undefined);
  if (있는.length < 2) return '<div class="empty">델타를 그릴 분기가 모자랍니다.</div>';
  const W = 340, H = height, pad = 4;
  const 크기 = 있는.map((x) => Math.abs(x.값));
  const hi = Math.max(...크기) || 1;
  const 폭 = (W - pad * 2) / 있는.length;
  const 막대 = 있는.map((x, i) => {
    const h = Math.max(2, (H - pad * 2) * (Math.abs(x.값) / hi));
    const x0 = pad + i * 폭 + 폭 * 0.15;
    const w = 폭 * 0.7;
    const y = pad + (H - pad * 2) - h;
    let c = "#ff453a";                       // 값이 내려감
    if (x.상승) {
      c = x.가속 === true ? "#00c805"        // 가속 — 진초록
        : x.가속 === false ? "#1f7a34"       // 둔화 — 연초록
        : "#4a8f5c";                         // 판단 불가 — 중간
    }
    return `<rect x="${x0.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}"
      height="${h.toFixed(1)}" fill="${c}" rx="1"/>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${막대}</svg>`;
}

/** 장세 구간 말 — 실측 근거가 있는 구간만 이름을 붙입니다 */
function 장세말(폭) {
  if (폭 === null || 폭 === undefined) return { 말: "판단 불가", cls: "" };
  if (폭 < 20) return { 말: "약한 장세 — 종목 신호 신뢰 낮음", cls: "down" };
  if (폭 < 60) return { 말: "살아 있는 장세", cls: "up" };
  return { 말: "60% 이상 — 과거 사건이 적어 실측 없음", cls: "warn" };
}

function 종목줄(r) {
  const 점 = r.새완성 ? '<span class="dot"></span>' : "";
  const 테마 = r.테마 ? ` · ${esc(r.테마)}` : "";
  const 델타 = r.델타 === true ? " · 델타상승" : "";
  const 값 = r.이격도 === null ? "—" : `+${num(r.이격도)}%`;
  return `<a class="row" href="#/t/${esc(r.종목)}">
    <div class="l"><div class="sym">${esc(r.종목)}${점}</div>
    <div class="meta">${esc(r.묶음)}${테마} · 완성 ${esc(r.완성일)}${델타}</div></div>
    <div class="pill">${값}</div></a>`;
}

function 구성목록(구성) {
  if (!구성 || !구성.length) return '<div class="empty">구성 종목 없음</div>';
  return 구성.map((m) => {
    const 꼬리 = m.완성
      ? `완성 ${esc(m.완성)}${m.이격도 !== null ? ` · 이격도 ${num(m.이격도)}%` : ""}` +
        `${m.신호 ? " · 🔥" : ""}${m.델타 === true ? " · 델타상승" : ""}`
      : "최근 91일 완성 없음";
    return `<a class="row" href="#/t/${esc(m.종목)}">
      <div class="l"><div class="sym">${esc(m.종목)}</div>
      <div class="meta">${꼬리}</div></div>
      <div class="pill ${m.신호 ? "" : "off"}">${
        m.이격도 === null ? "—" : num(m.이격도) + "%"}</div></a>`;
  }).join("");
}

/* ── 화면: 주도 ──────────────────────────────────────── */
function 화면_주도() {
  const a = APP;
  const 장세 = 장세말(a.장세.폭);
  const 새완성 = a.신호종목.filter((r) => r.새완성);
  const 채택 = a.가설.filter((h) => h.판정 === "채택").map((h) => h.라벨);

  let html = `<div class="hero">
    <div class="big ${장세.cls}">${num(a.장세.폭)}%</div>
    <div class="cap">시장 정배열 폭 · 지난주 ${num(
      a.장세.폭 !== null && a.장세.직전폭 !== null
        ? a.장세.폭 - a.장세.직전폭 : null, 1, true)}%p — ${esc(장세.말)}</div>
    ${sparkline(a.장세.시계열, "#00c805")}
  </div>

  <div class="grid2">
    <div class="kpi"><div class="k">이번 주 새 완성</div>
      <div class="v ${새완성.length ? "up" : ""}">${
        새완성.length ? esc(새완성.slice(0, 3).map((r) => r.종목).join(" · ")) : "없음"}</div>
      <div class="s">7일 안 정배열 완성</div></div>
    <div class="kpi"><div class="k">채택된 신호</div>
      <div class="v">${채택.length ? esc(채택.join(" · ")) : "없음"}</div>
      <div class="s">${채택.length ? "" : "모든 표시는 관찰 — 매수 근거 아님"}</div></div>
  </div>`;

  html += `<h2>확인 신호<span class="n">늦지만 강함</span></h2>
  <div class="note">주가가 이미 돌아선(3개월 시장 대비 +) 묶음에서
  정배열 폭 40%p↑·이익 델타 폭 50%p↑ 급등이 <b>동시에</b> 나온 곳입니다.</div>`;
  if (!a.확인신호.length) {
    html += '<div class="empty">지금 확인 신호가 켜진 묶음이 없습니다.</div>';
  } else {
    a.확인신호.forEach((r) => {
      html += `<div class="card fire">
        <div class="t">🔥 ${esc(r.묶음)}</div>
        <div class="d">3개월 상대수익 ${num(r["3개월상대"], 1, true)}%p ·
        정배열 폭 ${num(r.직전정배열폭, 0)}% → <b>${num(r.정배열폭, 0)}%</b> ·
        이익 델타 폭 ${num(r.직전델타폭, 0)}% → <b>${num(r.델타폭, 0)}%</b></div></div>
      <details><summary>${esc(r.묶음)} 구성 종목 ${r.구성.length}개</summary>
        <div class="body">${구성목록(r.구성)}</div></details>`;
    });
  }
  if (a.한조건.length) {
    html += `<details><summary>한 조건만 채운 묶음 ${a.한조건.length}개 (아직 확인 아님)</summary>
      <div class="body">${a.한조건.map((r) => `<div class="d">· ${esc(r.묶음)} —
        ${esc(r.모자란것)} (정배열 ${num(r.정배열폭, 0)}% ·
        델타 ${num(r.델타폭, 0)}% · 3개월 ${num(r["3개월상대"], 0, true)}%p)</div>`).join("")}
      </div></details>`;
  }

  html += `<h2>관찰판<span class="n">이르지만 약함</span></h2>
  <div class="note">최근 91일 정배열 완성이 몰리는 묶음입니다 (판정 아님).</div>`;
  if (!a.관찰판.length) {
    html += '<div class="empty">최근 91일 안에 완성이 2개 이상 나온 묶음이 없습니다.</div>';
  } else {
    a.관찰판.slice(0, 6).forEach((g) => {
      html += `<details><summary>${esc(g.묶음)} — 완성 ${g.완성}개
        (신호 ${g.신호} · 델타상승 ${g.델타상승})</summary>
        <div class="body"><div class="d">마지막 완성 ${esc(g.마지막완성)}</div>
        ${구성목록(g.구성)}</div></details>`;
    });
  }

  // 150차 — 차례가 "새 완성 먼저 + 이격도순"으로 바뀌었는데 라벨만
  // "최신순"으로 남아 있었습니다. 화면 글자가 실제와 다르면 그 자체가
  // 거짓말입니다.
  html += `<h2>이격도 30%+ 완성 종목<span class="n">새 완성·이격도순</span></h2>`;
  if (!a.신호종목.length) {
    html += '<div class="empty">지금 이격도 30%+ 완성 종목이 없습니다 — 없는 것은 없다고 말합니다.</div>';
  } else {
    html += a.신호종목.slice(0, 8).map(종목줄).join("");
    if (a.신호종목.length > 8) {
      html += `<details><summary>나머지 ${a.신호종목.length - 8}개 보기</summary>
        <div class="body">${a.신호종목.slice(8).map(종목줄).join("")}</div></details>`;
    }
    html += `<div class="honest">과거 실측(126차 백테스트): 이 신호의
      <b>1년 뒤 시장 +20%p 도달 ${a.실측.완성이격도30_1년}%</b>
      (아무 완성이나 잡으면 ${a.실측.완성기준선_1년}%) ·
      1년 뒤 시장을 이긴 비율 ${a.실측.이긴비율_1년}%.
      <b>채택 전 참고입니다</b> — 10건 중 4건은 시장에 졌습니다.</div>`;
  }
  return html;
}

/* ── 화면: 장세 ──────────────────────────────────────── */
function 화면_장세() {
  const a = APP;
  const 장세 = 장세말(a.장세.폭);
  let html = `<div class="hero">
    <div class="big ${장세.cls}">${num(a.장세.폭)}%</div>
    <div class="cap">시장 전체 정배열 폭 (최근 2년) — ${esc(장세.말)}</div>
    ${sparkline(a.장세.시계열, "#00c805", 130)}</div>
  <div class="honest">주봉 종가가 4주·13주·26주·52주선 위에 차례로 선 종목의
  비율입니다. 종목 신호를 읽기 전에 먼저 보는 배경입니다 (헌법 4조 — 장세가
  종목 선택을 지배합니다).</div>

  <h2>섹터별 정배열 폭</h2>
  <div class="honest">이 <b>폭</b>과 홈 화면의 <b>완성</b>은 <b>서로 다른 잣대</b>입니다
  — 일부러 그렇게 두었습니다(각각 33차·39차에 따로 등록한 정의라 한쪽에 맞추면
  사전 등록이 무너집니다). <b>폭</b>은 <b>주봉 종가가 4주선 위</b>일 것까지
  요구하고, <b>완성</b>은 <b>이동평균선의 배열만</b> 봅니다. 그래서 차트로는
  정배열인데 주가가 살짝 눌린 종목은 <b>완성에는 세어지고 폭에는 안 세어집니다</b>.
  어느 한쪽이 틀린 것이 아닙니다.${
    (a.잣대차이 && a.잣대차이.종목수)
      ? `<br>지금 그런 종목 <b>${a.잣대차이.종목수}개</b>: ${
          esc(a.잣대차이.종목들.slice(0, 6).join(" · "))}${
          a.잣대차이.종목수 > 6 ? ` 외 ${a.잣대차이.종목수 - 6}개` : ""}`
      : ""}</div>`;
  const 잰것 = a.섹터폭.filter((r) => r.폭 !== null);
  if (!잰것.length) {
    html += '<div class="empty">아직 잴 수 있는 섹터가 없습니다.</div>';
  } else {
    잰것.sort((x, y) => y.폭 - x.폭);
    html += 잰것.map((r) => `<div class="bar">
      <div class="nm">${esc(r.섹터)}</div>
      <div class="track"><div class="fill" style="width:${Math.max(2, r.폭)}%"></div></div>
      <div class="vl">${num(r.폭, 0)}%</div></div>`).join("");
    html += `<details><summary>섹터별 자세히 (상태·과거 실측)</summary><div class="body">
      ${잰것.map((r) => `<div class="card"><div class="t">${esc(r.섹터)}
        ${num(r.폭, 0)}% <span class="n">(${r.종목수}종목)</span></div>
        <div class="d">${esc(r.상태)} — ${esc(r.구간)} · 이 구간의 과거 1년
        폭등률 ${num(r.구간실측, 1)}%<br>${esc(r.구간설명 || "")}</div></div>`).join("")}
      </div></details>`;
  }
  const 안됨 = a.섹터폭.filter((r) => r.폭 === null);
  if (안됨.length) {
    html += `<div class="honest">판단 불가(이력 부족) 섹터 ${안됨.length}개:
      ${esc(안됨.map((r) => r.섹터).join(" · "))}</div>`;
  }
  return html;
}

/* ── 화면: 종목 ──────────────────────────────────────── */
function 화면_종목() {
  const a = APP;
  const 완성표 = {};
  a.완성전체.forEach((r) => { 완성표[r.종목] = r; });
  const 유지표 = {};
  a.정배열유지.forEach((r) => { 유지표[r.종목] = r; });

  const 목록 = a.정배열유지.map((r) => ({
    종목: r.종목, 묶음: r.묶음, 이격도: r.이격도, 정배열: true,
    완성: 완성표[r.종목],
  }));
  const 나머지 = Object.keys(완성표).filter((t) => !유지표[t]).map((t) => ({
    종목: t, 묶음: 완성표[t].묶음, 이격도: 완성표[t].이격도,
    정배열: false, 완성: 완성표[t],
  }));

  // 150차-O — 정배열도 아니고 최근 완성도 없는 종목은 **목록에 아예
  // 없었습니다.** 검색해도 안 나와서 "자료가 없다"로 오해합니다.
  // 자료가 있는 종목은 **전부** 넣습니다(a.종목목록).
  const 담긴 = new Set(목록.concat(나머지).map((r) => r.종목));
  const 조용한 = (a.종목목록 || []).filter((t) => !담긴.has(t)).map((t) => ({
    종목: t, 묶음: (a.묶음표 || {})[t] || "", 이격도: null,
    정배열: false, 완성: null,
  }));

  const 전체 = 목록.concat(나머지).concat(조용한);
  let html = `<input class="search" id="q" placeholder="종목 코드로 찾기 (예: NVDA)"
    autocomplete="off">
  <div class="note">지금 <b>주봉 정배열을 유지 중</b>인 종목 ${a.정배열유지.length}개를
  이격도 큰 순으로 먼저 보여 줍니다. 그 아래로 <b>자료가 있는 나머지
  ${조용한.length + 나머지.length}개</b>도 검색됩니다 — 정배열이 깨진 종목(조정 중)도
  찾아볼 수 있습니다.</div>
  <div id="list">${전체.map(종목간단줄).join("")}</div>
  <div id="없음" style="display:none"></div>`;
  return html;
}

/** 검색해도 아무것도 안 나올 때 — **없다고 정직하게 말하고 방법을 준다**
 *
 * 150차-O(주인 요청): "없는 종목을 치면 그 자리에서 긁어와서 판단까지."
 * 웹앱은 정적 파일이라 브라우저에서 SEC 에 접속할 수 없습니다(서버 없음
 * · CORS). 토큰을 넣어 자동 실행하게 만들면 공개 페이지에 저장소 열쇠를
 * 두는 셈이라 더 나쁩니다. 그래서 **누르면 실행 화면으로 가는 링크**를
 * 줍니다 — 열쇠가 필요 없고, 한 종목이면 몇 분입니다.
 */
function 못찾음판(말) {
  const 코드 = (말 || "").replace(/[^A-Z.\-]/g, "").slice(0, 8);
  const 주소 = "https://github.com/j2h5516-star/Model/actions/workflows/collect_one.yml";
  return `<div class="card"><div class="t">${esc(코드)} — 자료가 없습니다</div>
    <div class="d">아직 이 종목을 수집한 적이 없습니다. <b>지금 수집</b>하면
    몇 분 뒤 이 화면에서 볼 수 있습니다.<br><br>
    <a class="back" href="${주소}" target="_blank" rel="noopener">▶ 지금 수집하기</a>
    <br><span class="n">GitHub → Run workflow → 종목 코드에
    <b>${esc(코드)}</b> 입력 → Run</span><br><br>
    <b>왜 화면에서 바로 안 되나</b>: 이 앱은 서버 없는 정적 페이지라
    브라우저가 SEC 에 직접 접속할 수 없습니다. 자동 실행하려면 저장소
    열쇠를 이 공개 페이지에 넣어야 하는데 그건 더 위험합니다.<br><br>
    <b>미리 알아 둘 것</b>: 이렇게 받아 온 종목은 <b>가설 판정 표본에
    쓰이지 않습니다</b> — 궁금해서 부른 종목을 표본에 넣으면 "결과를 보고
    종목을 고르는 것"이 되어 사전 등록이 무너집니다.</div></div>`;
}

function 종목간단줄(r) {
  const 꼬리 = r.정배열 ? "정배열 유지 중" : "정배열 아님";
  const 완성 = r.완성 ? ` · 완성 ${esc(r.완성.완성일)}` : "";
  const 신호 = r.완성 && r.완성.신호 ? '<span class="badge new">신호</span>' : "";
  return `<a class="row" data-sym="${esc(r.종목)}" href="#/t/${esc(r.종목)}">
    <div class="l"><div class="sym">${esc(r.종목)}${신호}</div>
    <div class="meta">${esc(r.묶음)} · ${꼬리}${완성}</div></div>
    <div class="pill ${r.정배열 ? "" : "off"}">${
      r.이격도 === null || r.이격도 === undefined ? "—" : num(r.이격도) + "%"}</div></a>`;
}

/* ── 화면: 검증 ──────────────────────────────────────── */
function 화면_검증() {
  const a = APP;
  const 배지 = (판정) => 판정 === "채택" ? '<span class="badge adopt">채택</span>'
    : 판정 === "판정 불가" ? '<span class="badge wait">판정 대기</span>'
    : '<span class="badge no">미채택</span>';
  let html = `<h2>등록된 가설 ${a.가설.length}개</h2>
  <div class="note">데이터를 보기 <b>전에</b> 문턱·표적을 적어 두고, 로봇이 매일
  자동으로 다시 판정합니다. 채택 기준은 신호의 95% 구간 하한이 기준선 상한보다
  높을 때(완전 분리)이며, 표본 10건 미만은 "판정 대기"입니다.</div>`;
  html += a.가설.map((h) => `<div class="card">
    <div class="t">${esc(h.라벨)} ${배지(h.판정)}</div>
    <div class="d">신호 ${h.신호율 === null ? "—" : num(h.신호율) + "%"}
      (n=${h.신호n ?? "—"}) · 기준선 ${h.기준선율 === null ? "—" : num(h.기준선율) + "%"}
      ${h.등록일 ? ` · 등록 ${esc(h.등록일)}` : ""}
      ${h.탐색n ? `<br>탐색 표본(참고): ${num(h.탐색율)}% (n=${h.탐색n})` : ""}
      ${h.채택거리 ? `<br>채택까지: ${esc(h.채택거리)}${
          h.가장이른날 ? ` — 표적 창${h.창_거래일 ? `(${h.창_거래일}거래일)` : ""} 때문에 <b>${
            esc(h.가장이른날)}</b> 이전에는 나올 수 없습니다` : ""}` : ""}
      ${h.설명 ? `<br>${bold(h.설명)}` : ""}</div></div>`).join("");

  if (a.건강 && a.건강["채움률"]) {
    const c = a.건강["채움률"];
    const 어제 = a.건강["어제 대비"];
    html += `<h2>수집물 건강검진</h2>
    <div class="card"><div class="d">
      행 ${(c["행"] || 0).toLocaleString()}개 · 매출 ${c.revenue?.비율 ?? "—"}% ·
      조정EPS ${c.adj_eps?.비율 ?? "—"}% · GAAP ${c.gaap_eps?.비율 ?? "—"}%<br>
      ${어제 ? `어제 대비 바뀐 칸 ${(어제["바뀐 칸"] || 0).toLocaleString()}개 ·
        새 분기 ${어제["새 분기"] || 0}개` : "어제 수집물이 없어 맞대지 못했습니다."}
    </div></div>`;
  }
  html += `<div class="honest">한계(감추지 않습니다): 이 앱의 모든 확률은
    <b>과거 실측</b>이며 미래를 보장하지 않습니다. 종목 명단은 지금 존재하는
    회사에서 골라 <b>생존 편향</b>이 있습니다. 채택된 신호가 없으면 어떤 표시도
    매수 근거가 아닙니다.</div>`;
  return html;
}

/* ── 화면: 종목 상세 ─────────────────────────────────── */
async function 화면_상세(sym) {
  let d = 종목캐시[sym];
  if (!d) {
    try {
      const res = await fetch(TICK(sym));
      if (!res.ok) throw new Error("없음");
      d = await res.json();
      종목캐시[sym] = d;
    } catch (e) {
      return `<a class="back" href="#/stocks">‹ 종목</a>
        <div class="empty">${esc(sym)} 자료를 찾지 못했습니다.</div>`;
    }
  }
  const 마지막 = d.주봉.length ? d.주봉[d.주봉.length - 1][1] : null;
  let html = `<a class="back" href="#/stocks">‹ 종목</a>`;
  // 150차-O — 요청으로 받아 온 종목은 **판정 표본이 아니라고** 화면이
  // 먼저 말해야 합니다. 안 적으면 다른 종목과 같은 무게로 읽힙니다.
  if (d.요청수집) {
    html += `<div class="honest"><b>요청 수집분</b>${
      d.수집시각 ? ` (${esc(d.수집시각)})` : ""} — ${bold(esc(d.안내 || ""))}</div>`;
  }
  html += `
  <div class="detail-hd"><div class="sym">${esc(d.종목)}</div>
    <div class="cap">${esc(d.섹터)} · ${esc(d.묶음)}</div></div>
  <div class="hero"><div class="big ${d.지금정배열 ? "up" : ""}">${
    d.이격도 === null ? "—" : num(d.이격도) + "%"}</div>
    <div class="cap">52주선 대비 이격도 ·
      ${d.지금정배열 ? "지금 주봉 정배열 유지 중" : "지금 정배열 아님(조정 중)"}
      ${d.신호 ? ' · <b class="up">이격도 30%+ 완성 신호</b>' : ""}</div>
    ${twoLines(d.주봉, d.ma52)}
    <div class="cap">주봉 종가(초록)와 52주 이동평균(회색 점선) · 최근 ${
      d.주봉.length}주 · 마지막 ${num(마지막, 2)}</div></div>`;

  const 델타 = d.델타흐름 || [];
  const 그린것 = 델타.filter((x) => x.값 !== null && x.값 !== undefined);
  html += `<h2>이익 델타 흐름 <span class="n">${esc(d.잣대 || "잣대 없음")}</span></h2>`;
  if (그린것.length < 2) {
    html += `<div class="empty">델타를 그릴 분기가 모자랍니다 — 없는 것은
      없다고 말합니다.</div>`;
  } else {
    const 오른것 = 그린것.filter((x) => x.상승).length;
    const 성장있는 = 그린것.filter((x) => x.성장률 !== null && x.성장률 !== undefined);
    const 최근성장 = 성장있는.slice(-5);
    const 둔화중 = 최근성장.length >= 2 &&
      최근성장[최근성장.length - 1].성장률 < 최근성장[0].성장률;
    html += 델타막대(델타) +
      `<div class="cap">막대 하나가 분기 하나 · 높이는 그 분기의 값 ·
      <b class="up">진초록</b>=올랐고 성장이 <b>빨라짐</b> ·
      <span style="color:#1f7a34"><b>연초록</b></span>=올랐지만 성장이
      <b>느려짐</b> · <b class="down">빨강</b>=내렸음 ·
      최근 ${그린것.length}분기 중 <b>${오른것}분기 상승</b></div>` +
      (최근성장.length >= 2
        ? `<div class="cap">전분기 대비 성장률 흐름: <b>${
            최근성장.map((x) => num(x.성장률, 0) + "%").join(" → ")}</b>${
            둔화중 ? ' — <b class="down">둔화 중</b>' : ""}</div>`
        : "") +
      `<div class="honest"><b>값이 올라도 성장은 꺾일 수 있습니다.</b>
      1 → 10 → 20 은 셋 다 "상승"이지만 성장은 +900% → +100% 로 반토막입니다.
      그래서 색을 셋으로 나눕니다.<br>
      이 그래프는 <b>분기 대 분기</b>를 봅니다. 아래 실적 이력의 TTM 은
      <b>네 분기의 합</b>이라, 한 분기가 빠지면 델타는 나오는데 TTM 만
      "—"가 됩니다 — 둘이 다른 것을 재기 때문이지 고장이 아닙니다.</div>`;
  }

  html += `<h2>정배열 완성 이력</h2>`;
  if (!d.완성이력.length) {
    html += '<div class="empty">수집 기간 안에 완성 사건이 없습니다.</div>';
  } else {
    html += `<table class="mini"><tr><th>완성일</th><th>이격도</th>
      <th>델타</th><th>60일</th><th>1년</th></tr>` +
      d.완성이력.slice().reverse().map((e) => `<tr>
        <td>${esc(e.완성일)}</td><td>${num(e.이격도)}%</td>
        <td>${e.델타 === true ? "상승" : e.델타 === false ? "하락" : "—"}</td>
        <td class="${e.초과60 > 0 ? "up" : e.초과60 < 0 ? "down" : ""}">${
          num(e.초과60, 0, true)}</td>
        <td class="${e.초과250 > 0 ? "up" : e.초과250 < 0 ? "down" : ""}">${
          num(e.초과250, 0, true)}</td></tr>`).join("") + `</table>
      <div class="honest">60일·1년은 완성 다음 거래일에 사서 그만큼 들고 있었을 때
      시장(SPY)보다 몇 %p 더/덜 벌었는지입니다. 창이 아직 안 끝났으면 "—".</div>`;
  }

  html += `<h2>실적 이력 <span class="n">${esc(d.잣대 || "잣대 없음")}</span></h2>`;
  if (!d.실적.length) {
    html += `<div class="empty">이 종목은 잣대(조정 EPS→EBITDA→GAAP)를 채운
      분기가 8개 미만이라 측정에서 빠집니다 — 없는 것은 없다고 말합니다.</div>`;
  } else {
    html += `<table class="mini"><tr><th>발표일</th><th>TTM</th>
      <th>신기록</th><th>폭</th></tr>` +
      d.실적.slice().reverse().slice(0, 12).map((s) => `<tr>
        <td>${esc(s.발표일)}</td><td>${num(s.ttm, 2)}</td>
        <td>${s.첫돌파 ? '<b class="up">첫 돌파</b>' : s.신고점 ? "신기록" : "—"}</td>
        <td>${s.신고점폭 === null ? "—" : num(s.신고점폭, 0) + "%"}</td></tr>`).join("") +
      `</table>`;
  }
  return html;
}

/* ── 라우팅 ──────────────────────────────────────────── */
async function 그리기() {
  const hash = location.hash || "#/home";
  const view = $("#view");
  const [, 길, 인자] = hash.split("/");
  document.querySelectorAll("#tabs a").forEach((el) => {
    el.classList.toggle("on", el.dataset.tab === (길 || "home"));
  });
  if (!APP) { view.innerHTML = '<div class="loading">불러오는 중…</div>'; return; }

  if (길 === "t" && 인자) {
    view.innerHTML = '<div class="loading">불러오는 중…</div>';
    view.innerHTML = await 화면_상세(decodeURIComponent(인자));
  } else if (길 === "market") view.innerHTML = 화면_장세();
  else if (길 === "stocks") {
    view.innerHTML = 화면_종목();
    const q = $("#q");
    if (q) q.addEventListener("input", () => {
      const 말 = q.value.trim().toUpperCase();
      let 보인것 = 0;
      document.querySelectorAll("#list .row").forEach((el) => {
        const 보임 = !말 || el.dataset.sym.includes(말);
        el.style.display = 보임 ? "" : "none";
        if (보임) 보인것 += 1;
      });
      const 판 = $("#없음");
      if (판) {
        const 없다 = 말.length >= 1 && 보인것 === 0;
        판.style.display = 없다 ? "" : "none";
        판.innerHTML = 없다 ? 못찾음판(말) : "";
      }
    });
  } else if (길 === "check") view.innerHTML = 화면_검증();
  else view.innerHTML = 화면_주도();
  window.scrollTo(0, 0);
}

async function 시작() {
  try {
    const res = await fetch(DATA, { cache: "no-cache" });
    if (!res.ok) throw new Error("데이터 없음");
    APP = await res.json();
    const 수집 = APP.수집 ? String(APP.수집).slice(0, 16).replace("T", " ") : "알 수 없음";
    $("#stamp").textContent =
      `로봇 수집 ${수집} UTC · ${APP.종목수}종목 · 기준일 ${APP.기준일}`;
  } catch (e) {
    $("#view").innerHTML = `<div class="empty">데이터를 불러오지 못했습니다
      (${esc(e.message)}). 로봇이 아직 웹앱 데이터를 만들지 않았을 수 있습니다.</div>`;
    return;
  }
  await 그리기();
}

window.addEventListener("hashchange", 그리기);
시작();
