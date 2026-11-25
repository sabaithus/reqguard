"""Встроенный локальный Web-сервер и интерфейс ReqGuard Studio для живой демонстрации."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from reqguard.config import load_settings
from reqguard.models import AuditReport
from reqguard.pipeline import run_audit_pipeline
from reqguard.reporter_html import render_html_report
from reqguard.reporter_md import render_markdown_report

STUDIO_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ReqGuard Studio — ИИ-агент проверки качества FinTech ТЗ</title>
  <style>
    :root {
      --bg: #070d19; --bg2: #0f172a; --panel: #151f32; --panel-soft: #1c2840;
      --border: #2a3954; --text: #f1f5f9; --muted: #94a3b8;
      --accent: #38bdf8; --accent2: #818cf8; --crit: #ef4444; --maj: #f59e0b; --min: #3b82f6; --ok: #10b981;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at top right, #13223f 0%, var(--bg) 55%);
      color: var(--text); line-height: 1.5; min-height: 100vh;
    }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 24px 20px 64px; }
    .topbar {
      display: flex; justify-content: space-between; align-items: center; gap: 16px;
      flex-wrap: wrap; margin-bottom: 22px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .logo {
      width: 42px; height: 42px; border-radius: 10px; display: grid; place-items: center;
      background: linear-gradient(135deg, var(--accent), var(--accent2)); font-weight: 800; color: #06101f; font-size: 18px;
    }
    .brand h1 { margin: 0; font-size: 20px; letter-spacing: -0.2px; }
    .brand small { color: var(--muted); display: block; font-size: 12.5px; }
    .std-badges { display: flex; gap: 8px; flex-wrap: wrap; }
    .badge {
      padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
      background: var(--panel); border: 1px solid var(--border); color: var(--accent);
    }

    .upload-panel {
      background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
      padding: 22px; margin-bottom: 22px; display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px;
    }
    @media (max-width: 860px) { .upload-panel { grid-template-columns: 1fr; } }
    .dropzone {
      border: 2px dashed #3b5278; border-radius: 12px; padding: 26px 18px; text-align: center;
      background: rgba(15, 23, 42, 0.65); cursor: pointer; transition: 0.2s;
    }
    .dropzone.drag { border-color: var(--accent); background: rgba(56, 189, 248, 0.08); }
    .dropzone strong { display: block; font-size: 16px; margin-bottom: 6px; }
    .dropzone span { color: var(--muted); font-size: 13px; }
    .controls { display: flex; flex-direction: column; justify-content: space-between; gap: 12px; }
    .mode-box {
      background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
    }
    .btn-row { display: flex; gap: 10px; flex-wrap: wrap; }
    button.btn {
      border: 1px solid var(--border); background: var(--panel-soft); color: var(--text);
      padding: 10px 16px; border-radius: 10px; font-weight: 600; cursor: pointer; font-size: 13.5px;
      transition: 0.15s;
    }
    button.btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
    button.btn.primary {
      background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #06101f; border: none;
    }
    button.btn.primary:hover:not(:disabled) { filter: brightness(1.08); color: #06101f; }
    button.btn:disabled { opacity: 0.45; cursor: not-allowed; }

    .progress-box {
      display: none; background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
      padding: 18px 20px; margin-bottom: 22px;
    }
    .progress-box.active { display: block; }
    .log-list { margin: 10px 0 0; padding-left: 18px; color: #cbd5e1; font-size: 13.5px; }
    .log-list li { margin-bottom: 4px; }

    .results { display: none; }
    .results.active { display: block; }
    .hero {
      background: linear-gradient(135deg, #16233b 0%, #0d1526 100%);
      border: 1px solid var(--border); border-radius: 16px; padding: 22px; margin-bottom: 18px;
    }
    .verdict-pill {
      display: inline-block; padding: 5px 14px; border-radius: 999px; font-weight: 700;
      font-size: 12.5px; color: #fff; margin-bottom: 10px;
    }
    .verdict-pill.bad { background: var(--crit); }
    .verdict-pill.warn { background: var(--maj); color: #111827; }
    .verdict-pill.ok { background: var(--ok); }
    .passport-row { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px; color: var(--muted); margin-top: 10px; }

    .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 18px; }
    .kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
    .kpi .num { font-size: 26px; font-weight: 800; }
    .kpi .lbl { font-size: 12.5px; color: var(--muted); }

    .biz-panel {
      background: rgba(56, 189, 248, 0.06); border: 1px solid rgba(56, 189, 248, 0.3);
      border-radius: 12px; padding: 14px 18px; margin-bottom: 18px; display: flex;
      justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;
    }

    .toolbar {
      display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px;
    }
    .tabs, .filters { display: flex; gap: 8px; flex-wrap: wrap; }
    .tab-btn, .flt-btn {
      background: var(--panel); border: 1px solid var(--border); color: var(--muted);
      padding: 7px 13px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600;
    }
    .tab-btn.active, .flt-btn.active { border-color: var(--accent); color: var(--accent); background: var(--panel-soft); }
    .search-inp {
      background: var(--panel); border: 1px solid var(--border); color: var(--text);
      padding: 8px 12px; border-radius: 8px; min-width: 240px; font-size: 13px;
    }

    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 14px; }
    .card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
    .sev { padding: 2px 9px; border-radius: 6px; font-size: 11.5px; font-weight: 700; color: #fff; }
    .sev.CRITICAL { background: var(--crit); }
    .sev.MAJOR { background: var(--maj); color: #111827; }
    .sev.MINOR { background: var(--min); }
    .tag { font-size: 11.5px; padding: 2px 8px; border-radius: 6px; background: var(--bg2); border: 1px solid var(--border); color: var(--muted); }
    .tag.hybrid { border-color: var(--ok); color: var(--ok); }
    .ev-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; margin: 10px 0; }
    .ev-box { background: #09101f; border-left: 3px solid var(--accent); padding: 9px 12px; border-radius: 6px; }
    .ev-loc { font-size: 11.5px; color: var(--accent); font-weight: 600; margin-bottom: 4px; }
    .ev-q { margin: 0; white-space: pre-wrap; font-family: Consolas, monospace; font-size: 12px; color: #e2e8f0; }
    .q-box {
      background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25);
      border-radius: 8px; padding: 10px 12px; margin: 10px 0; display: flex; justify-content: space-between; align-items: center; gap: 12px;
    }
    table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 10px; overflow: hidden; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    th { background: #0a1120; color: var(--muted); }
    pre.md-preview {
      background: #09101f; border: 1px solid var(--border); border-radius: 12px; padding: 16px;
      white-space: pre-wrap; font-family: Consolas, monospace; font-size: 12.5px; max-height: 70vh; overflow: auto;
    }
    .err-banner {
      display: none; background: rgba(239, 68, 68, 0.14); border: 1px solid var(--crit);
      color: #fecaca; padding: 12px 16px; border-radius: 10px; margin-bottom: 16px;
    }
    .err-banner.active { display: block; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">
        <div class="logo">RG</div>
        <div>
          <h1>ReqGuard Studio</h1>
          <small>Гибридный ИИ-аудит FinTech-требований с анти-галлюцинационной верификацией</small>
        </div>
      </div>
      <div class="std-badges">
        <span class="badge">ISO/IEC/IEEE 29148:2018</span>
        <span class="badge">IEEE Std 830-1998</span>
        <span class="badge">MD / PDF / DOCX</span>
      </div>
    </div>

    <div class="upload-panel">
      <div class="dropzone" id="dropzone">
        <input type="file" id="fileInput" accept=".md,.markdown,.pdf,.docx,.txt" hidden>
        <strong id="fileLabel">Перетащите файл ТЗ сюда или нажмите для выбора</strong>
        <span>Поддерживаются .md, .pdf (с текстовым слоем), .docx, .txt</span>
      </div>
      <div class="controls">
        <label class="mode-box">
          <span>
            <strong>Только статический скан (&lt; 1 сек)</strong><br>
            <small style="color:var(--muted)">Без вызова LLM API — проверка структуры, таблиц и REST API</small>
          </span>
          <input type="checkbox" id="structuralOnly" style="width:18px;height:18px;">
        </label>
        <div class="btn-row">
          <button class="btn primary" id="runBtn" disabled>Запустить аудит</button>
          <button class="btn" id="loadLatestBtn" style="display:none">Открыть эталонный отчёт</button>
        </div>
      </div>
    </div>

    <div class="err-banner" id="errBanner"></div>

    <div class="progress-box" id="progressBox">
      <strong>Выполняется проверка документа...</strong>
      <ul class="log-list" id="logList"></ul>
    </div>

    <div class="results" id="results">
      <div class="hero">
        <div class="verdict-pill bad" id="verdictPill">—</div>
        <h2 id="docTitle" style="margin:4px 0 8px">Отчёт</h2>
        <p id="verdictRationale" style="margin:0 0 8px;color:#cbd5e1"></p>
        <div class="passport-row" id="passportRow"></div>
      </div>

      <div class="kpi-grid" id="kpiGrid"></div>

      <div class="biz-panel">
        <div>
          <strong>Бизнес-ценность до старта разработки:</strong>
          <span id="bizSummaryText" style="color:#cbd5e1;margin-left:6px"></span>
        </div>
        <div class="btn-row">
          <button class="btn" id="dlMdBtn">Скачать report.md</button>
          <button class="btn" id="dlHtmlBtn">Скачать report.html</button>
          <button class="btn" id="dlJsonBtn">Скачать report.json</button>
        </div>
      </div>

      <div class="toolbar">
        <div class="tabs">
          <button class="tab-btn active" data-tab="set">Системные конфликты и API (<span id="cntSet">0</span>)</button>
          <button class="tab-btn" data-tab="atomic">Дефекты пунктов (<span id="cntAtomic">0</span>)</button>
          <button class="tab-btn" data-tab="reqs">Нарезка требований (<span id="cntReqs">0</span>)</button>
          <button class="tab-btn" data-tab="md">Markdown-отчёт</button>
        </div>
        <div class="filters" id="sevFilters">
          <button class="flt-btn active" data-sev="ALL">Все</button>
          <button class="flt-btn" data-sev="CRITICAL">Критичные</button>
          <button class="flt-btn" data-sev="MAJOR">Существенные</button>
          <button class="flt-btn" data-sev="MINOR">Незначительные</button>
          <input class="search-inp" id="searchInput" placeholder="Поиск по тексту или Ф-2, REQ-06...">
        </div>
      </div>

      <div id="tabContent"></div>
    </div>
  </div>

  <script>
    const state = { file: null, data: null, tab: 'set', sev: 'ALL', q: '' };
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    const runBtn = document.getElementById('runBtn');
    const loadLatestBtn = document.getElementById('loadLatestBtn');
    const errBanner = document.getElementById('errBanner');
    const progressBox = document.getElementById('progressBox');
    const logList = document.getElementById('logList');
    const results = document.getElementById('results');

    function esc(s) {
      return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function setFile(f) {
      if (!f) return;
      state.file = f;
      document.getElementById('fileLabel').textContent = 'Выбран файл: ' + f.name + ' (' + Math.round(f.size / 1024) + ' КБ)';
      runBtn.disabled = false;
    }
    dropzone.onclick = () => fileInput.click();
    fileInput.onchange = e => setFile(e.target.files[0]);
    dropzone.ondragover = e => { e.preventDefault(); dropzone.classList.add('drag'); };
    dropzone.ondragleave = () => dropzone.classList.remove('drag');
    dropzone.ondrop = e => { e.preventDefault(); dropzone.classList.remove('drag'); setFile(e.dataTransfer.files[0]); };

    function showError(msg) {
      errBanner.textContent = msg;
      errBanner.classList.add('active');
    }
    function clearError() {
      errBanner.classList.remove('active');
    }

    async function checkLatest() {
      try {
        const res = await fetch('/api/latest');
        const payload = await res.json();
        if (payload.available) {
          loadLatestBtn.style.display = 'inline-block';
          loadLatestBtn.onclick = () => renderResult(payload);
        }
      } catch (_) {}
    }
    checkLatest();

    runBtn.onclick = async () => {
      if (!state.file) return;
      clearError();
      runBtn.disabled = true;
      results.classList.remove('active');
      progressBox.classList.add('active');
      logList.innerHTML = '<li>Отправка файла на сервер...</li>';

      try {
        const buf = await state.file.arrayBuffer();
        let binary = '';
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        const b64 = btoa(binary);
        const resp = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: state.file.name,
            content_base64: b64,
            structural_only: document.getElementById('structuralOnly').checked
          })
        });
        const startData = await resp.json();
        if (!resp.ok) throw new Error(startData.error || 'Ошибка запуска');

        const jobId = startData.job_id;
        while (true) {
          await new Promise(r => setTimeout(r, 700));
          const jr = await fetch('/api/jobs/' + encodeURIComponent(jobId));
          const jd = await jr.json();
          logList.innerHTML = (jd.logs || []).map(l => '<li>' + esc(l) + '</li>').join('');
          if (jd.status === 'done') {
            progressBox.classList.remove('active');
            renderResult(jd);
            break;
          }
          if (jd.status === 'error') {
            throw new Error(jd.error || 'Ошибка выполнения пайплайна');
          }
        }
      } catch (e) {
        progressBox.classList.remove('active');
        showError(e.message);
      } finally {
        runBtn.disabled = false;
      }
    };

    function renderResult(payload) {
      state.data = payload;
      const r = payload.report;
      const p = r.passport;
      const s = r.summary;

      const pill = document.getElementById('verdictPill');
      pill.textContent = s.verdict;
      pill.className = 'verdict-pill ' + (s.critical_count > 0 ? 'bad' : (s.major_count >= 3 ? 'warn' : 'ok'));

      document.getElementById('docTitle').textContent = 'Аудит ТЗ: «' + p.document_title + '»';
      document.getElementById('verdictRationale').textContent = s.verdict_rationale;
      document.getElementById('passportRow').innerHTML =
        `<span>Файл: <b>${esc(p.source_file)}</b> (${esc(p.source_format)}, строк: ${p.total_lines})</span>` +
        `<span>Модель: <b>${esc(p.provider)} / ${esc(p.model_name)}</b></span>` +
        `<span>Версия: <b>${esc(p.agent_version)}</b></span>` +
        `<span>Верифицировано цитат: <b>${p.verified_quotes_count}</b> (отсеяно галлюцинаций: ${p.hallucinations_filtered})</span>` +
        `<span>Время: <b>${p.duration_sec} с</b></span>`;

      document.getElementById('kpiGrid').innerHTML =
        `<div class="kpi"><div class="num">${s.readiness_score}/100</div><div class="lbl">Индекс готовности ТЗ</div></div>` +
        `<div class="kpi"><div class="num">${s.total_requirements}</div><div class="lbl">Выделено требований</div></div>` +
        `<div class="kpi"><div class="num" style="color:var(--crit)">${s.critical_count}</div><div class="lbl">Критичных блокеров</div></div>` +
        `<div class="kpi"><div class="num" style="color:var(--maj)">${s.major_count}</div><div class="lbl">Существенных (Major)</div></div>` +
        `<div class="kpi"><div class="num" style="color:var(--min)">${s.minor_count}</div><div class="lbl">Незначительных (Minor)</div></div>`;

      document.getElementById('bizSummaryText').textContent =
        `Выявлено ${s.critical_count} блокеров и ${s.total_defects} дефектов до написания кода — предотвращены переделки между бэкендом, мобильной разработкой и QA.`;

      document.getElementById('cntSet').textContent = r.set_defects.length;
      document.getElementById('cntAtomic').textContent = r.atomic_defects.length;
      document.getElementById('cntReqs').textContent = r.requirements.length;

      results.classList.add('active');
      renderTab();
    }

    function downloadText(filename, content, mime) {
      const blob = new Blob([content], { type: mime });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
    }
    document.getElementById('dlMdBtn').onclick = () => state.data && downloadText('report.md', state.data.markdown, 'text/markdown;charset=utf-8');
    document.getElementById('dlHtmlBtn').onclick = () => state.data && downloadText('report.html', state.data.html, 'text/html;charset=utf-8');
    document.getElementById('dlJsonBtn').onclick = () => state.data && downloadText('report.json', JSON.stringify(state.data.report, null, 2), 'application/json;charset=utf-8');

    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.onclick = () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.tab = btn.dataset.tab;
        renderTab();
      };
    });
    document.querySelectorAll('.flt-btn').forEach(btn => {
      btn.onclick = () => {
        document.querySelectorAll('.flt-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.sev = btn.dataset.sev;
        renderTab();
      };
    });
    document.getElementById('searchInput').oninput = e => { state.q = e.target.value.toLowerCase(); renderTab(); };

    function fmtLoc(sp) {
      const pg = sp.page ? `Стр. ${sp.page}, ` : '';
      const ln = sp.line_start === sp.line_end ? `строка ${sp.line_start}` : `строки ${sp.line_start}–${sp.line_end}`;
      return pg + ln + (sp.section_path ? ` (${sp.section_path})` : '');
    }

    function copyTxt(btn, txt) {
      navigator.clipboard.writeText(txt);
      const old = btn.textContent;
      btn.textContent = 'Скопировано';
      setTimeout(() => btn.textContent = old, 1200);
    }

    function renderTab() {
      if (!state.data) return;
      const r = state.data.report;
      const host = document.getElementById('tabContent');
      const q = state.q;
      const sev = state.sev;

      if (state.tab === 'set') {
        const items = r.set_defects.filter(d =>
          (sev === 'ALL' || d.severity === sev) &&
          (!q || JSON.stringify(d).toLowerCase().includes(q))
        );
        host.innerHTML = items.map(d => `
          <div class="card">
            <div class="card-head">
              <span class="sev ${d.severity}">${d.severity}</span>
              <b>${esc(d.defect_id)}</b>
              <span class="tag">${esc(d.criterion)}</span>
              <span class="tag ${d.origin === 'HYBRID' ? 'hybrid' : ''}">${esc(d.origin)}</span>
              <span class="tag">Требования: ${esc((d.related_req_ids || []).join(', ') || 'Весь документ')}</span>
            </div>
            <h3 style="margin:4px 0 10px">${esc(d.title)}</h3>
            <div class="ev-grid">
              ${d.evidence.map(sp => `<div class="ev-box"><div class="ev-loc">${esc(fmtLoc(sp))}</div><pre class="ev-q">${esc(sp.quote)}</pre></div>`).join('')}
            </div>
            <p><b>Проблема:</b> ${esc(d.problem)}</p>
            <p><b>Риск:</b> ${esc(d.impact)}</p>
            <div class="q-box">
              <span><b>Вопрос аналитику:</b> ${esc(d.question_for_analyst)}</span>
              <button class="btn" onclick='copyTxt(this, ${JSON.stringify(d.question_for_analyst)})'>Копировать</button>
            </div>
            <p style="margin-bottom:0"><b>Рекомендация:</b> ${esc(d.recommendation)}</p>
          </div>
        `).join('') || '<div class="card">По текущему фильтру замечаний не найдено.</div>';
        return;
      }

      if (state.tab === 'atomic') {
        const items = r.atomic_defects.filter(d =>
          (sev === 'ALL' || d.severity === sev) &&
          (!q || JSON.stringify(d).toLowerCase().includes(q))
        );
        host.innerHTML = items.map(d => `
          <div class="card">
            <div class="card-head">
              <span class="sev ${d.severity}">${d.severity}</span>
              <b>${esc(d.defect_id)} → ${esc(d.req_id)}${d.doc_id ? ' (' + esc(d.doc_id) + ')' : ''}</b>
              <span class="tag">${esc(d.criterion)}</span>
              <span class="tag ${d.origin === 'HYBRID' ? 'hybrid' : ''}">${esc(d.origin)}</span>
            </div>
            ${d.evidence.map(sp => `<div class="ev-box"><div class="ev-loc">${esc(fmtLoc(sp))}</div><pre class="ev-q">${esc(sp.quote)}</pre></div>`).join('')}
            <p><b>Дефект:</b> ${esc(d.problem)}</p>
            <p><b>Риск:</b> ${esc(d.impact)}</p>
            <p style="margin-bottom:0"><b>Исправление:</b> ${esc(d.recommendation)}</p>
          </div>
        `).join('') || '<div class="card">По текущему фильтру дефектов не найдено.</div>';
        return;
      }

      if (state.tab === 'reqs') {
        const items = r.requirements.filter(req => !q || JSON.stringify(req).toLowerCase().includes(q));
        host.innerHTML = `<table><thead><tr><th>ID</th><th>ID в док.</th><th>Категория</th><th>Локация</th><th>Формулировка и цитата</th></tr></thead><tbody>` +
          items.map(req => `<tr><td><code>${esc(req.report_id)}</code></td><td>${esc(req.doc_id || '—')}</td><td>${esc(req.category)}</td><td>${esc(fmtLoc(req.source))}</td><td><b>${esc(req.title)}</b><div style="color:var(--muted);font-size:12px;margin-top:4px">${esc(req.source.quote)}</div></td></tr>`).join('') +
          `</tbody></table>`;
        return;
      }

      host.innerHTML = `<pre class="md-preview">${esc(state.data.markdown)}</pre>`;
    }
  </script>
</body>
</html>
"""


def create_web_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    env_file: Path | None = None,
    base_dir: Path | None = None,
) -> ThreadingHTTPServer:
    """Создаёт и настраивает локальный ThreadingHTTPServer для ReqGuard Studio."""
    root_dir = (base_dir or Path.cwd()).resolve()
    jobs_lock = threading.Lock()
    jobs: dict[str, dict[str, Any]] = {}

    class ReqGuardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, html_text: str) -> None:
            body = html_text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send_html(HTTPStatus.OK, STUDIO_HTML)
                return

            if path == "/api/latest":
                for rel in ("submission_ready/report.json", "runs/report.json"):
                    candidate = root_dir / rel
                    if candidate.exists():
                        try:
                            report = AuditReport.model_validate_json(candidate.read_text(encoding="utf-8"))
                            self._send_json(
                                HTTPStatus.OK,
                                {
                                    "available": True,
                                    "report": report.model_dump(),
                                    "markdown": render_markdown_report(report),
                                    "html": render_html_report(report),
                                },
                            )
                            return
                        except Exception:
                            pass
                self._send_json(HTTPStatus.OK, {"available": False})
                return

            if path.startswith("/api/jobs/"):
                job_id = path.removeprefix("/api/jobs/").strip()
                with jobs_lock:
                    job = jobs.get(job_id)
                    if job is None:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Задача не найдена"})
                        return
                    snapshot = dict(job)
                    snapshot["logs"] = list(job["logs"])
                self._send_json(HTTPStatus.OK, snapshot)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/api/analyze":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                req_json = json.loads(raw_body.decode("utf-8"))
                filename = Path(str(req_json.get("filename") or "requirements.md")).name
                content_b64 = str(req_json.get("content_base64") or "")
                structural_only = bool(req_json.get("structural_only", False))
                if not content_b64:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Пустое содержимое файла"})
                    return
                file_bytes = base64.b64decode(content_b64)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Некорректный запрос: {exc}"})
                return

            job_id = uuid.uuid4().hex[:12]
            with jobs_lock:
                jobs[job_id] = {
                    "status": "running",
                    "logs": ["Файл принят сервером. Подготовка анализа..."],
                    "error": None,
                }

            def _worker() -> None:
                def _log(msg: str) -> None:
                    with jobs_lock:
                        jobs[job_id]["logs"].append(msg)

                try:
                    settings = load_settings(env_file)
                    if structural_only:
                        settings.provider = "none"
                    with tempfile.TemporaryDirectory() as tmpdir:
                        in_file = Path(tmpdir) / filename
                        in_file.write_bytes(file_bytes)
                        report = run_audit_pipeline(
                            input_path=in_file,
                            settings=settings,
                            progress_cb=_log,
                            allow_structural_only=structural_only,
                        )
                    md_text = render_markdown_report(report)
                    html_text = render_html_report(report)
                    with jobs_lock:
                        jobs[job_id].update(
                            {
                                "status": "done",
                                "report": report.model_dump(),
                                "markdown": md_text,
                                "html": html_text,
                            }
                        )
                except Exception as exc:
                    with jobs_lock:
                        jobs[job_id]["status"] = "error"
                        jobs[job_id]["error"] = str(exc)

            threading.Thread(target=_worker, daemon=True).start()
            self._send_json(HTTPStatus.ACCEPTED, {"job_id": job_id})

    return ThreadingHTTPServer((host, port), ReqGuardHandler)


def run_web_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    env_file: Path | None = None,
    open_browser: bool = True,
    base_dir: Path | None = None,
) -> None:
    """Запускает локальный сервер ReqGuard Studio и обслуживает запросы до Ctrl+C."""
    server = create_web_server(host=host, port=port, env_file=env_file, base_dir=base_dir)
    url = f"http://{host}:{server.server_port}"
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
