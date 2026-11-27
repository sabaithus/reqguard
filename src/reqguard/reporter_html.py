"""HTML-отчёт и единая страница ReqGuard: вердикт, вопросы владельцу с доказательствами, все замечания.

Одна функция рисует и автономный файл отчёта, и главную страницу Studio (studio=True добавляет
панель «Проверить другое ТЗ»). Порядок блоков подчинён одному решению: можно ли передавать
документ в разработку и что спросить у владельца до старта.
"""

from __future__ import annotations

import html
import json

from reqguard.models import (
    AuditReport,
    CATEGORY_LABELS_RU,
    CRITERION_LABELS_RU,
    SEVERITY_LABELS_RU,
    SetDefect,
)

_e = html.escape

# Палитра: холодный нейтральный фон документа, чернильный текст, один интерактивный цвет (ссылки, кнопки).
# Красный/охра/серо-синий — только тяжесть замечания. Ссылки на источник набраны моноширинным,
# как сноски в документе.
_STYLE = """
:root {
  --bg: #f3f5f7; --surface: #ffffff; --ink: #17212b; --muted: #5a6775; --line: #d8dde3;
  --accent: #1d5bb8; --accent-ink: #ffffff; --quote-bg: #f7f8fa;
  --crit: #b4372b; --crit-bg: #fbeeec; --maj: #9a6412; --maj-bg: #fbf3e4; --min: #4b6584; --min-bg: #eef2f7;
  --ok: #2c7a4b; --ok-bg: #e9f5ee;
  --font-body: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
  --font-cite: "IBM Plex Mono", Consolas, "Cascadia Mono", monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #12171d; --surface: #1a2129; --ink: #e5eaef; --muted: #9ba7b3; --line: #2c3641;
  --accent: #82b1ff; --accent-ink: #0d1620; --quote-bg: #151b22;
  --crit: #ff8a7a; --crit-bg: #3a1f1c; --maj: #e5b263; --maj-bg: #33281a; --min: #9bb4d0; --min-bg: #1f2a36;
  --ok: #7fd3a0; --ok-bg: #18301f; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #12171d; --surface: #1a2129; --ink: #e5eaef; --muted: #9ba7b3; --line: #2c3641;
  --accent: #82b1ff; --accent-ink: #0d1620; --quote-bg: #151b22;
  --crit: #ff8a7a; --crit-bg: #3a1f1c; --maj: #e5b263; --maj-bg: #33281a; --min: #9bb4d0; --min-bg: #1f2a36;
  --ok: #7fd3a0; --ok-bg: #18301f; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 var(--font-body); }
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.page { max-width: 1080px; margin: 0 auto; padding-block: 0 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.topbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; padding-block: 14px; border-bottom: 1px solid var(--line); }
.brand { font-weight: 600; letter-spacing: .01em; }
.brand span { color: var(--muted); font-weight: 400; }
.topbar .spacer { flex: 1; }
.btn { font: inherit; font-weight: 600; border-radius: 6px; padding: 8px 14px; cursor: pointer; border: 1px solid var(--line); background: var(--surface); color: var(--ink); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.btn:disabled { opacity: .6; cursor: default; }
.verdict { padding-block: 32px 20px; }
.doc-name { color: var(--muted); margin: 0 0 6px; }
.verdict h1 { font-size: clamp(26px, 4vw, 38px); line-height: 1.15; margin: 0 0 12px; text-wrap: balance; font-weight: 600; text-transform: lowercase; }
.verdict h1::first-letter { text-transform: uppercase; }
.verdict h1.no { color: var(--crit); }
.verdict h1.partial { color: var(--maj); }
.verdict h1.go { color: var(--ok); }
.verdict .why { max-width: 68ch; margin: 0 0 14px; }
.counts { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 0; padding: 0; list-style: none; font-variant-numeric: tabular-nums; }
.counts b { font-size: 18px; }
.counts .c b { color: var(--crit); } .counts .m b { color: var(--maj); } .counts .n b { color: var(--min); }
.passport { margin-top: 14px; font-size: 13px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 4px 16px; }
.passport code { font-family: var(--font-cite); font-size: 12.5px; }
h2 { font-size: 21px; font-weight: 600; margin: 40px 0 6px; text-wrap: balance; }
h2 + .lead { color: var(--muted); margin: 0 0 16px; max-width: 70ch; }
.questions { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.q { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }
.q > details > summary { cursor: pointer; padding: 14px 16px; font-weight: 600; font-size: 16px; list-style: none; display: flex; gap: 12px; align-items: baseline; }
.q > details > summary::-webkit-details-marker { display: none; }
.q > details > summary::before { content: "+"; font-family: var(--font-cite); color: var(--muted); }
.q > details[open] > summary::before { content: "−"; }
.q .body { padding: 0 16px 16px 38px; display: grid; gap: 10px; }
.sev { display: inline-block; font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 4px; white-space: nowrap; }
.sev.CRITICAL { color: var(--crit); background: var(--crit-bg); }
.sev.MAJOR { color: var(--maj); background: var(--maj-bg); }
.sev.MINOR { color: var(--min); background: var(--min-bg); }
.cites { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 8px; }
.cite { margin: 0; background: var(--quote-bg); border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; min-width: 0; }
.cite .loc { font-family: var(--font-cite); font-size: 12px; color: var(--muted); display: block; margin-bottom: 4px; }
.cite .txt { white-space: pre-wrap; overflow-wrap: anywhere; }
.alts { margin: 0; padding-left: 20px; }
.qa { color: var(--muted); font-size: 14px; margin: 0; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 40px 0 0; }
.filters button { font: inherit; font-size: 14px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); padding: 6px 12px; border-radius: 6px; cursor: pointer; }
.filters button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); font-weight: 600; }
.finding { background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--line); border-radius: 8px; padding: 16px; margin-top: 12px; }
.finding[data-sev="CRITICAL"] { border-left-color: var(--crit); }
.finding[data-sev="MAJOR"] { border-left-color: var(--maj); }
.finding[data-sev="MINOR"] { border-left-color: var(--min); }
.finding .meta { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; font-size: 13px; color: var(--muted); }
.finding .id { font-family: var(--font-cite); font-weight: 600; color: var(--ink); }
.finding h3 { font-size: 17px; margin: 8px 0 10px; font-weight: 600; text-wrap: balance; }
.finding p { margin: 8px 0 0; max-width: 75ch; }
.finding .label { font-weight: 600; }
.ask { margin-top: 10px; padding: 10px 12px; border-radius: 6px; background: var(--min-bg); }
.fix { margin-top: 10px; padding: 10px 12px; border-radius: 6px; background: var(--ok-bg); }
.decision { margin: 12px 0 0; border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; min-width: 0; }
.decision select { width: 100%; text-overflow: ellipsis; }
.decision legend { font-weight: 600; padding: 0 4px; }
.decision select { font: inherit; max-width: 100%; margin-top: 4px; padding: 6px; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); }
.registry { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 9px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 13px; }
td code, .mono { font-family: var(--font-cite); font-size: 12.5px; }
.muted { color: var(--muted); }
.export { margin-top: 16px; }
details.more > summary { cursor: pointer; color: var(--accent); font-weight: 600; margin-top: 40px; }
.panel { margin-top: 12px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px; display: grid; gap: 12px; }
.panel[hidden] { display: none; }
.panel .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.panel label.check { display: flex; gap: 6px; align-items: center; font-size: 14px; }
.status { font-size: 14px; color: var(--muted); white-space: pre-wrap; margin: 0; }
.status.error { color: var(--crit); }
@media (prefers-reduced-motion: no-preference) { .finding:target, .q:target { animation: flash 1.2s ease-out; } }
@keyframes flash { from { box-shadow: 0 0 0 3px var(--accent); } to { box-shadow: 0 0 0 0 transparent; } }
"""

_FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
          '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
          '&family=IBM+Plex+Sans:wght@400;600&display=swap">')


def _where(span) -> str:
    """Короткая сноска «стр. 1 · строка 26»; полный путь раздела — во всплывающей подсказке."""
    lines = f"строка {span.line_start}" if span.line_start == span.line_end else f"строки {span.line_start}–{span.line_end}"
    return (f"стр. {span.page} · " if span.page else "") + lines


def _cite(span) -> str:
    section = span.section_path.split(" → ")[-1] if span.section_path else ""
    return (f'<figure class="cite"><span class="loc" title="{_e(span.section_path or "")}">{_e(_where(span))}'
            f'{" · " + _e(section) if section else ""}</span>'
            f'<span class="txt">«{_e(span.quote)}»</span></figure>')


def _decision_control(finding: SetDefect) -> str:
    decision = finding.decision
    if decision is None:
        return ""
    payload = {
        "defect_id": finding.defect_id, "question": finding.question_for_analyst,
        "evidence": [span.model_dump() for span in finding.evidence], **decision.model_dump(),
    }
    options = "".join(
        f'<option value="{i}">{_e(option)}</option>' for i, option in enumerate(decision.alternatives)
    )
    select_id = f"decision-{_e(finding.defect_id)}"
    return f"""<fieldset class="decision" data-case="{_e(json.dumps(payload, ensure_ascii=False), quote=True)}">
      <legend>Решение владельца требований</legend>
      <p>{_e(decision.scenario)}</p>
      <label for="{select_id}">Ожидаемый исход</label><br>
      <select id="{select_id}"><option value="">Ожидает решения</option>{options}
      <option value="deferred">Отложить решение</option></select>
      <p class="qa">Проверка QA после согласования: {_e(decision.acceptance_check)}</p>
    </fieldset>"""


def _verdict_class(report: AuditReport) -> str:
    s = report.summary
    if s.critical_count > 0 or s.can_start_development is None:
        return "no"
    return "go" if s.can_start_development else "partial"


def _studio_panel() -> str:
    return """
  <section class="panel" id="checkPanel" hidden aria-label="Проверка другого документа">
    <p style="margin:0">Загрузите ТЗ (PDF с текстовым слоем, DOCX, Markdown или TXT). Агент нарежет требования,
      проверит каждое и весь набор, затем покажет результат на этой странице. Полный анализ занимает 1–3 минуты.</p>
    <div class="row">
      <input type="file" id="docFile" accept=".pdf,.docx,.md,.txt">
      <label class="check"><input type="checkbox" id="structuralOnly"> Только детерминированные проверки, без модели</label>
    </div>
    <div class="row">
      <input type="file" id="answersFile" accept=".json" aria-label="Протокол решений владельца (необязательно)">
      <span class="muted" style="font-size:14px">Необязательно: протокол решений владельца для повторной проверки</span>
    </div>
    <div class="row"><button class="btn primary" id="runBtn" type="button">Проверить документ</button></div>
    <p class="status" id="runStatus" role="status"></p>
  </section>"""


_STUDIO_SCRIPT = """
const panel = document.getElementById('checkPanel');
document.getElementById('openCheck').addEventListener('click', e => {
  panel.hidden = !panel.hidden; e.currentTarget.setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden) document.getElementById('docFile').focus();
});
const statusEl = document.getElementById('runStatus');
function say(text, isError) { statusEl.textContent = text; statusEl.classList.toggle('error', !!isError); }
async function toBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer()); let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
document.getElementById('runBtn').addEventListener('click', async () => {
  const file = document.getElementById('docFile').files[0];
  if (!file) { say('Выберите файл документа.', true); return; }
  const btn = document.getElementById('runBtn'); btn.disabled = true;
  try {
    const answers = document.getElementById('answersFile').files[0];
    const body = { filename: file.name, content_base64: await toBase64(file),
      structural_only: document.getElementById('structuralOnly').checked,
      answers: answers ? JSON.parse(await answers.text()) : null };
    say('Документ отправлен. Идёт анализ…');
    const start = await fetch('/api/analyze', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const job = await start.json();
    if (!start.ok) throw new Error(job.error || 'Сервер не принял документ.');
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      const res = await fetch('/api/jobs/' + encodeURIComponent(job.job_id));
      const data = await res.json();
      if (data.status === 'done') { location.href = '/report/' + encodeURIComponent(job.job_id); return; }
      if (data.status === 'error') throw new Error(data.error || 'Анализ завершился ошибкой.');
      say((data.logs || []).slice(-1)[0] || 'Идёт анализ…');
    }
  } catch (err) { say(err.message + ' Проверьте файл и ключ модели в .env, затем повторите.', true); btn.disabled = false; }
});
"""


def render_empty_studio() -> str:
    """Главная страница Studio, когда сохранённого отчёта ещё нет."""
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ReqGuard Studio</title>{_FONTS}<style>{_STYLE}</style></head>
<body><div class="page">
  <header class="topbar"><div class="brand">ReqGuard Studio <span>· проверка ТЗ до старта разработки</span></div>
    <div class="spacer"></div><button class="btn primary" id="openCheck" type="button" aria-expanded="true">Проверить ТЗ</button></header>
  <section class="verdict"><h1>Сохранённых проверок пока нет</h1>
    <p class="why">Загрузите документ с требованиями: результат появится здесь, а файлы отчёта сохранятся в папке runs.</p></section>
  {_studio_panel().replace(' hidden aria-label', ' aria-label')}
</div><script>{_STUDIO_SCRIPT}</script></body></html>"""


def render_html_report(report: AuditReport, studio: bool = False) -> str:
    p = report.passport
    s = report.summary
    findings = [*report.set_defects, *report.atomic_defects]

    questions = []
    for question, issues in report.owner_questions():
        worst = min(issues, key=lambda i: ["CRITICAL", "MAJOR", "MINOR"].index(i.severity))
        body = []
        for issue in issues:
            ref = _e(issue.defect_id)
            body.append(f'<p style="margin:0"><a href="#{ref}" class="mono">{ref}</a> · '
                        f'<span class="sev {issue.severity}">{_e(SEVERITY_LABELS_RU[issue.severity])}</span></p>')
            body.append('<div class="cites">' + "".join(_cite(sp) for sp in issue.evidence) + "</div>")
            decision = getattr(issue, "decision", None)
            if decision:
                options = "".join(f"<li>{_e(option)}</li>" for option in decision.alternatives)
                body.append(f'<p style="margin:0" class="label">Варианты решения</p><ol class="alts">{options}</ol>'
                            f'<p class="qa">Проверка QA: {_e(decision.acceptance_check)}</p>')
        is_open = " open" if worst.severity == "CRITICAL" else ""
        questions.append(f'<li class="q"><details{is_open}><summary><span class="sev {worst.severity}">'
                         f'{_e(SEVERITY_LABELS_RU[worst.severity])}</span><span>{_e(question)}</span></summary>'
                         f'<div class="body">{"".join(body)}</div></details></li>')

    def finding_card(d, atomic: bool) -> str:
        crit = _e(CRITERION_LABELS_RU.get(d.criterion, d.criterion))
        if atomic:
            target = _e(d.req_id + (f" ({d.doc_id})" if d.doc_id else ""))
            head = f'<h3>{_e(d.problem)}</h3>'
            text = (f'<p><span class="label">Риск:</span> {_e(d.impact)}</p>'
                    f'<p><span class="label">Что исправить:</span> {_e(d.recommendation)}</p>')
            if d.suggested_fix:
                text += f'<div class="fix"><span class="label">Предлагаемая правка:</span> {_e(d.suggested_fix)}</div>'
            extra = ""
        else:
            target = _e(", ".join(d.related_req_ids) if d.related_req_ids else "весь документ")
            head = f'<h3>{_e(d.title)}</h3>'
            text = (f'<p><span class="label">Проблема:</span> {_e(d.problem)}</p>'
                    f'<p><span class="label">Риск:</span> {_e(d.impact)}</p>'
                    f'<div class="ask"><span class="label">Вопрос владельцу:</span> {_e(d.question_for_analyst)}</div>'
                    f'<p><span class="label">Рекомендация:</span> {_e(d.recommendation)}</p>')
            extra = _decision_control(d)
        return (f'<article class="finding" id="{_e(d.defect_id)}" data-sev="{d.severity}">'
                f'<div class="meta"><span class="id">{_e(d.defect_id)}</span>'
                f'<span class="sev {d.severity}">{_e(SEVERITY_LABELS_RU[d.severity])}</span>'
                f'<span>{crit}</span><span>затронуто: {target}</span><span>источник: {_e(d.origin)}</span></div>'
                f'{head}<div class="cites">{"".join(_cite(sp) for sp in d.evidence)}</div>{text}{extra}</article>')

    set_cards = "".join(finding_card(d, False) for d in report.set_defects)
    atomic_cards = "".join(finding_card(d, True) for d in report.atomic_defects)

    req_rows = "".join(
        f'<tr><td><code>{_e(r.report_id)}</code></td><td>{_e(r.doc_id) if r.doc_id else "<span class=muted>присвоен</span>"}</td>'
        f'<td>{_e(CATEGORY_LABELS_RU.get(r.category, r.category))}</td><td class="mono" title="{_e(r.source.section_path or '')}">{_e(_where(r.source))}</td>'
        f'<td>{_e(r.title)}<div class="muted" style="font-size:13px">«{_e(r.source.quote)}»</div></td></tr>'
        for r in report.requirements)

    fixes = "".join(f"<li>{_e(f)}</li>" for f in s.priority_fixes)
    export_button = ('<button class="btn export" type="button" onclick="exportDecisions()">Скачать протокол решений (JSON)</button>'
                     if any(d.decision for d in report.set_defects) else "")

    history, history_items = [], []
    for accepted in report.accepted_decisions:
        finding = accepted.finding
        history.append({"question": finding.question_for_analyst, "evidence": [sp.model_dump() for sp in finding.evidence],
                        "status": "selected", "selected_outcome": accepted.outcome})
        history_items.append(f"<li>{_e(finding.question_for_analyst)} — <b>{_e(accepted.outcome)}</b>"
                             f" · {_e(accepted.exported_at)} · QA: {_e(finding.decision.acceptance_check)}</li>")
    history_html = ("<h2>Принятые решения владельца</h2><ol>" + "".join(history_items) + "</ol>") if history else ""
    critic_html = ""
    if p.critic_status != "off":
        notes = "".join(f"<li>{_e(f'{n.action} · {n.problem} — {n.reason}. {n.evidence.format_locator()}: {n.evidence.quote}')}</li>"
                        for n in report.critic_notes)
        critic_html = f"<h2>Контрольный проход модели</h2><p>{_e(p.critic_status)}</p><ul>{notes}</ul>"

    studio_button = ('<button class="btn primary" id="openCheck" type="button" aria-expanded="false" '
                     'aria-controls="checkPanel">Проверить другое ТЗ</button>') if studio else ""
    brand = "ReqGuard Studio" if studio else "ReqGuard"
    title = f"ReqGuard Отчёт — {_e(p.document_title)}"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  {_FONTS}
  <style>{_STYLE}</style>
</head>
<body>
<div class="page" id="audit" data-source="{_e(p.source_sha256)}" data-version="{_e(p.agent_version)}" data-run="{_e(p.run_timestamp)}">
  <header class="topbar">
    <div class="brand">{brand} <span>· проверка ТЗ до старта разработки</span></div>
    <div class="spacer"></div>{studio_button}
  </header>
  {_studio_panel() if studio else ""}

  <section class="verdict">
    <p class="doc-name">{_e(p.document_title)} · {_e(p.source_file)}</p>
    <h1 class="{_verdict_class(report)}">{_e(s.verdict)}</h1>
    <p class="why">{_e(s.verdict_rationale)}</p>
    <ul class="counts">
      <li class="c"><b>{s.critical_count}</b> критичных</li>
      <li class="m"><b>{s.major_count}</b> существенных</li>
      <li class="n"><b>{s.minor_count}</b> незначительных</li>
      <li><b>{s.total_requirements}</b> требований проверено</li>
    </ul>
    <div class="passport">
      <span>{_e(p.status_label)}</span>
      <span>Модель: {_e(p.provider)} / {_e(p.model_name)}</span>
      <span>Агент: <code>{_e(p.agent_version)}</code></span>
      <span>Прогон: {_e(p.run_timestamp)}, {p.duration_sec:.0f} с</span>
      <span>Цитат проверено кодом: {p.verified_quotes_count}, отклонено записей модели: {p.hallucinations_filtered}</span>
      <span>Индекс готовности {s.score_label} (эвристика по числу и тяжести замечаний)</span>
    </div>
  </section>

  <div id="accepted" data-history="{_e(json.dumps(history, ensure_ascii=False), quote=True)}">{history_html}</div>
  {critic_html}
  <h2>Вопросы владельцу продукта</h2>
  <p class="lead">Ответы на эти вопросы нужны до старта. У каждого вопроса — цитаты из документа со страницей и строкой; вопросы по блокерам раскрыты.</p>
  <ol class="questions">{''.join(questions) or '<li class="q"><p style="padding:14px 16px;margin:0">Открытых вопросов в результате этой проверки нет.</p></li>'}</ol>
  {export_button}
  <div class="filters">
    <button type="button" aria-pressed="true" data-sev="ALL">Все замечания · {s.total_defects}</button>
    <button type="button" aria-pressed="false" data-sev="CRITICAL">Критичные · {s.critical_count}</button>
    <button type="button" aria-pressed="false" data-sev="MAJOR">Существенные · {s.major_count}</button>
    <button type="button" aria-pressed="false" data-sev="MINOR">Незначительные · {s.minor_count}</button>
  </div>

  <h2 id="decisions">Проверка всего набора требований · {len(report.set_defects)}</h2>
  <p class="lead">Противоречия между разделами, пропуски и несогласованные контракты (ISO/IEC/IEEE 29148 п. 5.2.6, IEEE 830 п. 4.3).</p>
  {set_cards}

  <h2>Проверка каждого требования · {len(report.atomic_defects)}</h2>
  <p class="lead">Однозначность, полнота, проверяемость и атомарность отдельных формулировок (ISO/IEC/IEEE 29148 п. 5.2.5).</p>
  {atomic_cards}

  <h2>Что исправить в первую очередь</h2>
  <ol>{fixes}</ol>

  <details class="more"><summary>Реестр выделенных требований · {len(report.requirements)}</summary>
    <div class="registry"><table>
      <thead><tr><th>ID в отчёте</th><th>ID в документе</th><th>Категория</th><th>Где</th><th>Формулировка и цитата</th></tr></thead>
      <tbody>{req_rows}</tbody>
    </table></div>
  </details>
</div>
<script>
  const audit = document.getElementById('audit').dataset;
  for (const field of document.querySelectorAll('.decision')) {{
    const select = field.querySelector('select');
    const key = ['reqguard', audit.source, audit.version, audit.run, JSON.parse(field.dataset.case).defect_id].join(':');
    try {{ select.value = localStorage.getItem(key) || ''; }} catch (_) {{}}
    select.addEventListener('change', () => {{ try {{ localStorage.setItem(key, select.value); }} catch (_) {{}} }});
  }}
  function exportDecisions() {{
    const decisions = Array.from(document.querySelectorAll('.decision'), field => {{
      const entry = JSON.parse(field.dataset.case);
      const choice = field.querySelector('select').value;
      entry.status = choice === '' ? 'pending' : choice === 'deferred' ? 'deferred' : 'selected';
      entry.selected_outcome = entry.status === 'selected' ? entry.alternatives[Number(choice)] : null;
      return entry;
    }});
    const record = {{source_sha256: audit.source, agent_version: audit.version, run_timestamp: audit.run,
      exported_at: new Date().toISOString(), decisions: [...JSON.parse(document.getElementById('accepted').dataset.history), ...decisions]}};
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {{type: 'application/json'}}));
    const link = document.createElement('a');
    link.href = url; link.download = 'reqguard-decisions-' + audit.source.slice(0, 12) + '.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }}
  for (const btn of document.querySelectorAll('.filters button')) {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.filters button').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
      document.querySelectorAll('.finding').forEach(c => {{ c.hidden = !(btn.dataset.sev === 'ALL' || c.dataset.sev === btn.dataset.sev); }});
    }});
  }}
  {_STUDIO_SCRIPT if studio else ""}
</script>
</body>
</html>
"""
