"""Генератор автономного интерактивного HTML-отчёта для наглядной презентации и ревью аналитиками."""

from __future__ import annotations

import html

from reqguard.models import (
    AuditReport,
    CATEGORY_LABELS_RU,
    CRITERION_LABELS_RU,
    SEVERITY_LABELS_RU,
)


def render_html_report(report: AuditReport) -> str:
    p = report.passport
    s = report.summary

    badge_color = "#dc2626" if s.critical_count > 0 else ("#d97706" if s.can_start_development is None or s.major_count >= 3 else "#16a34a")

    req_rows_html = []
    for r in report.requirements:
        doc_id = html.escape(r.doc_id) if r.doc_id else "<span class='muted'>— (присвоен)</span>"
        loc = html.escape(r.source.format_locator())
        cat = html.escape(CATEGORY_LABELS_RU.get(r.category, r.category))
        title = html.escape(r.title)
        quote = html.escape(r.source.quote)
        req_rows_html.append(
            f"<tr><td><code>{r.report_id}</code></td><td>{doc_id}</td><td>{cat}</td>"
            f"<td class='loc'>{loc}</td><td><strong>{title}</strong><div class='quote'>«{quote}»</div></td></tr>"
        )

    set_cards_html = []
    for sd in report.set_defects:
        sev_cls = sd.severity.lower()
        sev_label = html.escape(SEVERITY_LABELS_RU[sd.severity])
        crit_label = html.escape(CRITERION_LABELS_RU.get(sd.criterion, sd.criterion))
        ev_items = "".join(
            f"<div class='ev-box'><div class='ev-loc'>{html.escape(sp.format_locator())}</div>"
            f"<pre class='ev-quote'>{html.escape(sp.quote)}</pre></div>"
            for sp in sd.evidence
        )
        rel = ", ".join(sd.related_req_ids) if sd.related_req_ids else "Весь документ"
        set_cards_html.append(
            f"""
            <div class="card defect-card" data-sev="{sd.severity}">
              <div class="card-header">
                <span class="sev-pill {sev_cls}">{sev_label}</span>
                <span class="def-id">{html.escape(sd.defect_id)}</span>
                <span class="crit-tag">{crit_label}</span>
                <span class="origin-tag">{html.escape(sd.origin)}</span>
              </div>
              <h3>{html.escape(sd.title)}</h3>
              <p class="meta-rel">Затронуто: <code>{html.escape(rel)}</code></p>
              <div class="ev-grid">{ev_items}</div>
              <p><strong>Проблема:</strong> {html.escape(sd.problem)}</p>
              <p><strong>Бизнес- и тех. риск:</strong> {html.escape(sd.impact)}</p>
              <div class="analyst-q"><strong>Вопрос аналитику / PO:</strong> {html.escape(sd.question_for_analyst)}</div>
              <p><strong>Рекомендация:</strong> {html.escape(sd.recommendation)}</p>
            </div>
            """
        )

    atomic_cards_html = []
    for ad in report.atomic_defects:
        sev_cls = ad.severity.lower()
        sev_label = html.escape(SEVERITY_LABELS_RU[ad.severity])
        crit_label = html.escape(CRITERION_LABELS_RU.get(ad.criterion, ad.criterion))
        tag = f"{ad.req_id}" + (f" ({ad.doc_id})" if ad.doc_id else "")
        ev_items = "".join(
            f"<div class='ev-box'><div class='ev-loc'>{html.escape(sp.format_locator())}</div>"
            f"<pre class='ev-quote'>{html.escape(sp.quote)}</pre></div>"
            for sp in ad.evidence
        )
        fix_block = (
            f"<div class='fix-box'><strong>Предлагаемая правка:</strong> {html.escape(ad.suggested_fix)}</div>"
            if ad.suggested_fix
            else ""
        )
        atomic_cards_html.append(
            f"""
            <div class="card defect-card" data-sev="{ad.severity}">
              <div class="card-header">
                <span class="sev-pill {sev_cls}">{sev_label}</span>
                <span class="def-id">{html.escape(ad.defect_id)} → {html.escape(tag)}</span>
                <span class="crit-tag">{crit_label}</span>
                <span class="origin-tag">{html.escape(ad.origin)}</span>
              </div>
              {ev_items}
              <p><strong>Суть дефекта:</strong> {html.escape(ad.problem)}</p>
              <p><strong>Риск:</strong> {html.escape(ad.impact)}</p>
              <p><strong>Что исправить:</strong> {html.escape(ad.recommendation)}</p>
              {fix_block}
            </div>
            """
        )

    fixes_li = "".join(f"<li>{html.escape(f)}</li>" for f in s.priority_fixes)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ReqGuard Отчёт — {html.escape(p.document_title)}</title>
  <style>
    :root {{
      --bg: #0f172a; --panel: #1e293b; --border: #334155; --text: #f1f5f9; --muted: #94a3b8;
      --crit: #ef4444; --maj: #f59e0b; --min: #3b82f6; --ok: #10b981; --accent: #38bdf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.55; }}
    .container {{ max-width: 1220px; margin: 0 auto; padding: 28px 20px 60px; }}
    .hero {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid var(--border); border-radius: 14px; padding: 24px; margin-bottom: 24px; }}
    .verdict-banner {{ display: inline-block; padding: 6px 14px; border-radius: 999px; font-weight: 700; font-size: 13px; background: {badge_color}; color: #fff; margin-bottom: 10px; }}
    h1 {{ margin: 6px 0 12px; font-size: 26px; }}
    h2 {{ margin-top: 34px; border-bottom: 1px solid var(--border); padding-bottom: 8px; font-size: 20px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 14px; margin: 20px 0; }}
    .kpi {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }}
    .kpi .num {{ font-size: 28px; font-weight: 800; }}
    .kpi .lbl {{ font-size: 13px; color: var(--muted); }}
    .passport {{ font-size: 13px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; }}
    .filters {{ display: flex; gap: 10px; margin: 16px 0; flex-wrap: wrap; }}
    .filter-btn {{ background: var(--panel); color: var(--text); border: 1px solid var(--border); padding: 7px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; }}
    .filter-btn.active {{ border-color: var(--accent); color: var(--accent); }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 16px; }}
    .card-header {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }}
    .sev-pill {{ padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; color: #fff; }}
    .sev-pill.critical {{ background: var(--crit); }}
    .sev-pill.major {{ background: var(--maj); color: #111827; }}
    .sev-pill.minor {{ background: var(--min); }}
    .def-id {{ font-family: monospace; font-weight: 700; color: var(--accent); }}
    .crit-tag, .origin-tag {{ font-size: 12px; padding: 2px 8px; border-radius: 6px; background: #0f172a; border: 1px solid var(--border); color: var(--muted); }}
    .ev-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; margin: 12px 0; }}
    .ev-box {{ background: #0b1120; border-left: 3px solid var(--accent); padding: 10px 12px; border-radius: 6px; }}
    .ev-loc {{ font-size: 12px; color: var(--accent); font-weight: 600; margin-bottom: 4px; }}
    .ev-quote {{ margin: 0; white-space: pre-wrap; font-family: Consolas, monospace; font-size: 12.5px; color: #e2e8f0; }}
    .analyst-q {{ background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.35); padding: 10px 14px; border-radius: 8px; margin: 10px 0; }}
    .fix-box {{ background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.35); padding: 10px 14px; border-radius: 8px; margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 10px; overflow: hidden; font-size: 13.5px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: #0b1120; color: var(--muted); font-weight: 600; }}
    .quote {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
    .muted {{ color: var(--muted); }}
    code {{ background: #0b1120; padding: 2px 6px; border-radius: 4px; color: var(--accent); }}
  </style>
</head>
<body>
  <div class="container">
    <div class="hero">
      <div class="verdict-banner">{html.escape(s.verdict)}</div>
      <h1>Аудит качества ТЗ: «{html.escape(p.document_title)}»</h1>
      <p>{html.escape(p.status_label)}. {html.escape(s.verdict_rationale)}</p>
      <div class="passport">
        <span>Файл: <strong>{html.escape(p.source_file)}</strong> ({html.escape(p.source_format)}, {p.total_lines} строк)</span>
        <span>Модель: <strong>{html.escape(p.provider)} / {html.escape(p.model_name)}</strong></span>
        <span>Версия агента: <strong>{html.escape(p.agent_version)}</strong></span>
        <span>Верифицировано цитат: <strong>{p.verified_quotes_count}</strong> (отклонено записей: {p.hallucinations_filtered})</span>
        <span>Время: <strong>{p.duration_sec:.2f} с</strong></span>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="kpi"><div class="num">{s.score_label}</div><div class="lbl">Индекс готовности ТЗ</div></div>
      <div class="kpi"><div class="num">{s.total_requirements}</div><div class="lbl">Выделено требований</div></div>
      <div class="kpi"><div class="num" style="color:var(--crit)">{s.critical_count}</div><div class="lbl">Критичных блокеров</div></div>
      <div class="kpi"><div class="num" style="color:var(--maj)">{s.major_count}</div><div class="lbl">Существенных (Major)</div></div>
      <div class="kpi"><div class="num" style="color:var(--min)">{s.minor_count}</div><div class="lbl">Незначительных (Minor)</div></div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Что исправить в первую очередь до старта разработки</h3>
      <ol>{fixes_li}</ol>
    </div>

    <div class="filters">
      <button class="filter-btn active" onclick="filterCards('ALL', this)">Все нарушения ({s.total_defects})</button>
      <button class="filter-btn" onclick="filterCards('CRITICAL', this)">Критичные блокеры ({s.critical_count})</button>
      <button class="filter-btn" onclick="filterCards('MAJOR', this)">Существенные ({s.major_count})</button>
      <button class="filter-btn" onclick="filterCards('MINOR', this)">Незначительные ({s.minor_count})</button>
    </div>

    <h2>Проверка всего набора требований и контрактов ({len(report.set_defects)})</h2>
    {''.join(set_cards_html)}

    <h2>Индивидуальная проверка требований ({len(report.atomic_defects)})</h2>
    {''.join(atomic_cards_html)}

    <h2>Реестр выделенных требований ({len(report.requirements)})</h2>
    <table>
      <thead><tr><th>ID</th><th>ID в док.</th><th>Категория</th><th>Локация</th><th>Формулировка и верифицированная цитата</th></tr></thead>
      <tbody>{''.join(req_rows_html)}</tbody>
    </table>
  </div>
  <script>
    function filterCards(sev, btn) {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.defect-card').forEach(c => {{
        c.style.display = (sev === 'ALL' || c.dataset.sev === sev) ? 'block' : 'none';
      }});
    }}
  </script>
</body>
</html>
"""
