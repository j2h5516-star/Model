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

  html += `<h2>이격도 30%+ 완성 종목<span class="n">최신순</span></h2>`;
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

  <h2>섹터별 정배열 폭</h2>`;
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

  const 전체 = 목록.concat(나머지);
  let html = `<input class="search" id="q" placeholder="종목 코드로 찾기 (예: LITE)"
    autocomplete="off">
  <div class="note">지금 <b>주봉 정배열을 유지 중</b>인 종목 ${a.정배열유지.length}개를
  이격도 큰 순으로 먼저 보여 줍니다. 여기 없는 종목은 지금 배열이 깨져 있다는
  뜻입니다 (예: 조정 중).</div>
  <div id="list">${전체.map(종목간단줄).join("")}</div>`;
  return html;
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
  let html = `<a class="back" href="#/stocks">‹ 종목</a>
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
      document.querySelectorAll("#list .row").forEach((el) => {
        el.style.display = !말 || el.dataset.sym.includes(말) ? "" : "none";
      });
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
