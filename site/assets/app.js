(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    data: null,
    history: { snapshots: [], news_archive: [] },
    selectedDate: null,
    selectedPattern: null,
    chartDays: 180,
    horizon: 5,
    onlyCurrentPatterns: false,
    search: '',
    category: 'all',
    chartObserver: null,
  };

  const REGIME = {
    risk_on: { label: 'リスク選好', description: 'QQQの直近上昇やVIXの低位など、相対的にリスク選好と分類された局面です。' },
    risk_off: { label: 'リスク回避', description: 'QQQの下落やVIXの高水準など、リスク回避色が強いと分類された局面です。' },
    high_volatility: { label: '高ボラティリティ', description: 'VIXの上昇・高水準、またはTQQQの実現ボラティリティを基に変動が大きいと分類された局面です。' },
    neutral: { label: '中立・混合', description: '設定した条件で強いリスク選好／回避に該当しない局面です。' },
  };
  const CATEGORY = {
    monetary_policy: '金融政策・金利', inflation: 'インフレ', employment: '雇用', technology: 'テクノロジー',
    geopolitics_trade: '地政学・関税', earnings: '企業業績', market_volatility: '市場変動', macro_growth: '景気',
  };
  const PATTERN_DESC = {
    morning_star: '下落後に小さな実体を挟み、上昇足が戻す3本構成の反転候補。',
    evening_star: '上昇後に小さな実体を挟み、下落足が押し戻す3本構成の反転候補。',
    three_white_soldiers: '3本続けて終値が切り上がる陽線群。相場環境によって結果は異なります。',
    three_black_crows: '3本続けて終値が切り下がる陰線群。相場環境によって結果は異なります。',
    bullish_engulfing: '前日の実体を当日の陽線実体が包む形。', bearish_engulfing: '前日の実体を当日の陰線実体が包む形。',
    piercing_line: '前日の陰線に対し、当日陽線が実体の中点を上回って引ける形。',
    dark_cloud_cover: '前日の陽線に対し、当日陰線が実体の中点を下回って引ける形。',
    bullish_harami: '大きな陰線の実体内に当日の陽線実体が収まる形。',
    bearish_harami: '大きな陽線の実体内に当日の陰線実体が収まる形。',
    hammer: '下ヒゲが実体より長く、直近が下落・中立局面の場合に分類する形。',
    hanging_man: '下ヒゲが長く、直近が上昇局面の場合に分類する形。',
    inverted_hammer: '上ヒゲが長く、直近が下落・中立局面の場合に分類する形。',
    shooting_star: '上ヒゲが長く、直近が上昇局面の場合に分類する形。',
    bullish_marubozu: '実体が高値・安値の範囲の大部分を占める陽線。',
    bearish_marubozu: '実体が高値・安値の範囲の大部分を占める陰線。',
    doji: '始値と終値が近く、実体が値幅に対して小さい形。',
    spinning_top: '実体が小さく、上下にヒゲを持つ形。',
    bullish_candle: '終値が始値を上回った日足。パターン固有の優位性とは別に比較する基準です。',
    bearish_candle: '終値が始値を下回った日足。パターン固有の優位性とは別に比較する基準です。',
  };

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function n(value, digits = 2) {
    if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
    return Number(value).toLocaleString('ja-JP', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function usd(value) { return value == null || !Number.isFinite(Number(value)) ? '—' : '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function pct(value, digits = 2, signed = false) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    const num = Number(value);
    return (signed && num > 0 ? '+' : '') + num.toLocaleString('ja-JP', { minimumFractionDigits: digits, maximumFractionDigits: digits }) + '%';
  }
  function pctClass(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '';
    return Number(value) > 0 ? 'positive-text' : Number(value) < 0 ? 'negative-text' : '';
  }
  function dateLabel(date, withYear = true) {
    if (!date) return '—';
    const d = new Date(`${date}T12:00:00Z`);
    if (Number.isNaN(d.getTime())) return String(date);
    return new Intl.DateTimeFormat('ja-JP', withYear ? { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'UTC' } : { month: '2-digit', day: '2-digit', timeZone: 'UTC' }).format(d);
  }
  function dateTimeLabel(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat('ja-JP', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
  }
  function gradeClass(grade) { return `grade-${String(grade || 'D').toLowerCase()}`; }
  function regimeLabel(regime) { return REGIME[regime]?.label || '中立・混合'; }
  function directionLabel(direction) {
    return ({ bullish: '上昇方向', bearish: '下落方向', neutral: '中立', unknown: '判定なし' })[direction] || '判定なし';
  }
  function patternLabel(id) {
    const item = state.data?.pattern_statistics?.find(p => p.id === id);
    return item?.label || id || '分類なし';
  }
  function formatGrade(grade) { return grade || 'D'; }
  function safeUrl(url) {
    try {
      const parsed = new URL(url);
      return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : null;
    } catch { return null; }
  }
  function linkHtml(url, title, className = '') {
    const safe = safeUrl(url);
    if (!safe) return `<span class="${esc(className)}">${esc(title)}</span>`;
    return `<a class="${esc(className)}" href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(title)} <span aria-hidden="true">↗</span></a>`;
  }
  function showToast(message) {
    const node = $('#toast');
    if (!node) return;
    node.textContent = message;
    node.classList.add('show');
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => node.classList.remove('show'), 3200);
  }
  function setStatus(type, title, detail) {
    const dot = $('#data-status-dot');
    const side = $('.sidebar-bottom .status-dot');
    [dot, side].forEach(node => {
      if (!node) return;
      node.classList.remove('ready', 'error');
      if (type === 'ready') node.classList.add('ready');
      if (type === 'error') node.classList.add('error');
    });
    $('#data-status-title').textContent = title;
    $('#data-status-detail').textContent = detail;
    $('#sidebar-status').textContent = type === 'ready' ? 'データ取得済み' : type === 'error' ? 'データを確認できません' : 'データを確認中';
  }

  async function fetchJson(url) {
    const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`${url} の読み込みに失敗しました (HTTP ${response.status})`);
    return response.json();
  }

  async function loadData({ announce = false } = {}) {
    if (announce) showToast('公開済みデータを再読込しています。');
    setStatus('loading', 'データを読み込んでいます', '生成済みJSONを確認中');
    try {
      const data = await fetchJson('data/latest.json');
      if (data && data.status === 'not_generated') {
        setStatus('loading', '初回データの生成待ち', 'GitHub Actionsで「Run workflow」を実行してください');
        $('#sidebar-status').textContent = '初回データ生成待ち';
        $('#price-chart').innerHTML = '<div class="empty-state">実データはまだ生成されていません。GitHubのActionsタブから「Update TQQQ analysis and deploy Pages」を手動実行してください。サンプル価格は表示していません。</div>';
        $('#pattern-table-body').innerHTML = '<tr><td colspan="9" class="table-empty">初回のデータ生成後に統計が表示されます。</td></tr>';
        $('#news-list').innerHTML = '<div class="empty-state compact">初回のデータ生成後にニュース参照が表示されます。</div>';
        $('#ai-citations').innerHTML = '<div class="empty-state compact">Gemini検証と出典参照は初回データ生成後に表示されます。</div>';
        $('#last-generated').textContent = '分析データの更新日時：未生成';
        return;
      }
      if (!data || data.status !== 'ok' || !Array.isArray(data.bars)) throw new Error('分析JSONの形式が想定と異なります。');
      let history = { schema_version: 1, snapshots: [], news_archive: [] };
      try { history = await fetchJson('data/history.json'); } catch (err) { console.warn('History data is not available yet:', err.message); }
      if (!Array.isArray(history.snapshots)) history.snapshots = [];
      if (!Array.isArray(history.news_archive)) history.news_archive = [];
      state.data = data;
      state.history = history;
      if (!state.selectedDate || !data.bars.some(b => b.date === state.selectedDate)) state.selectedDate = data.bars[data.bars.length - 1]?.date || data.as_of_date;
      if (!state.selectedPattern || !data.pattern_statistics.some(p => p.id === state.selectedPattern)) state.selectedPattern = data.current_patterns?.[0] || data.pattern_statistics?.[0]?.id || null;
      const jumpDate = $('#jump-date');
      if (jumpDate) {
        jumpDate.min = data.data_range?.start || '';
        jumpDate.max = data.data_range?.end || '';
        jumpDate.value = state.selectedDate || data.as_of_date || '';
      }
      renderAll();
      setStatus('ready', '分析データを表示中', `最終取引日 ${dateLabel(data.as_of_date)} · ${Number(data.data_range?.rows || 0).toLocaleString('ja-JP')}営業日`);
      $('#market-date').textContent = `最終取引日 ${dateLabel(data.as_of_date)}`;
      $('#last-generated').textContent = `分析データの更新日時：${dateTimeLabel(data.generated_at)}`;
      if (announce) showToast('公開済み分析データを更新しました。');
    } catch (error) {
      console.error(error);
      setStatus('error', 'データを読み込めません', 'Actionsの更新状況と data/latest.json を確認してください');
      $('#price-chart').innerHTML = '<div class="empty-state">分析データを読み込めません。GitHub Actionsの実行結果と data/latest.json を確認してください。</div>';
      if (announce) showToast('データを再読込できませんでした。');
    }
  }

  function renderAll() {
    if (!state.data) return;
    renderOverview();
    renderConfidence();
    renderChart();
    renderSelectedDate();
    renderPatternTable();
    renderPatternDetail();
    renderNews();
    renderContext();
    renderAiReview();
    renderValidation();
  }

  function renderOverview() {
    const d = state.data;
    $('#metric-close').textContent = usd(d.quote?.close);
    $('#metric-close-foot').textContent = `対象日：${dateLabel(d.as_of_date)}`;
    const change = $('#metric-change');
    change.textContent = pct(d.quote?.change_pct, 2, true);
    change.classList.toggle('positive', Number(d.quote?.change_pct) > 0);
    change.classList.toggle('negative', Number(d.quote?.change_pct) < 0);
    $('#metric-change-foot').textContent = `前日終値 ${usd(d.quote?.previous_close)}`;
    $('#metric-rows').textContent = Number(d.data_range?.rows || 0).toLocaleString('ja-JP');
    $('#metric-range').textContent = `${dateLabel(d.data_range?.start)} — ${dateLabel(d.data_range?.end)}`;
    $('#metric-regime').textContent = regimeLabel(d.quote?.market_regime);
    $('#metric-regime-foot').textContent = `QQQ ${usd(d.quote?.qqq_close)} / VIX ${n(d.quote?.vix_close, 2)}`;
    $('#confidence-target').textContent = `対象足：${d.confidence?.statistics?.pattern_label || '—'} · ${dateLabel(d.as_of_date)}`;
  }

  function renderConfidence() {
    const d = state.data;
    const stat = d.confidence?.statistics || {};
    const ctx = d.confidence?.context || {};
    const ai = d.confidence?.gemini || {};
    $('#stat-grade').className = `grade-badge ${gradeClass(stat.grade)}`;
    $('#stat-grade').textContent = formatGrade(stat.grade);
    $('#stat-summary').textContent = `${stat.pattern_label || '対象足'}の過去${stat.sample_count ?? 0}回を、5営業日後の値動きで確認。`;
    $('#stat-samples').textContent = Number(stat.sample_count || 0).toLocaleString('ja-JP');
    $('#stat-hit-label').textContent = stat.direction === 'neutral' ? '±0.5%以内' : '方向一致率';
    $('#stat-hit').textContent = pct(stat.hit_rate_pct, 1);
    $('#stat-return').textContent = pct(stat.avg_return_pct, 2, true);
    $('#stat-return').className = pctClass(stat.avg_return_pct);
    $('#stat-foot').textContent = stat.ci95_low_pct != null ? `95%区間（方向一致率）：${n(stat.ci95_low_pct, 1)}–${n(stat.ci95_high_pct, 1)}%` : '十分なサンプルがないため、信頼度はデータ不足扱いです。';

    $('#context-grade').className = `grade-badge ${gradeClass(ctx.grade)}`;
    $('#context-grade').textContent = formatGrade(ctx.grade);
    $('#context-summary').textContent = ctx.evidence_basis === 'matching_pattern_regime_and_news_categories'
      ? `同じ足・${regimeLabel(ctx.market_regime)}・重複するニュースカテゴリの過去例を比較。`
      : `同じ${regimeLabel(ctx.market_regime)}の過去例を比較。ニュースカテゴリが一致する保存例はまだ少数です。`;
    $('#context-samples').textContent = Number(ctx.sample_count || 0).toLocaleString('ja-JP');
    $('#context-hit').textContent = pct(ctx.hit_rate_pct, 1);
    $('#context-hit').className = pctClass(ctx.hit_rate_pct);
    $('#context-regime').textContent = regimeLabel(ctx.market_regime);
    const cats = (ctx.current_event_categories || []).map(c => CATEGORY[c] || c);
    $('#context-foot').textContent = `イベント一致例：${Number(ctx.event_matched_samples || 0)}件 · 保存済みニュースカテゴリ：${cats.length ? cats.slice(0, 4).join('、') : '該当タグなし'}。初期は市場局面統計が中心です。`;

    $('#ai-grade').className = `grade-badge ${gradeClass(ai.grade)}`;
    $('#ai-grade').textContent = formatGrade(ai.grade);
    $('#ai-summary').textContent = ai.summary || 'Gemini検証結果はありません。';
    $('#ai-direction').textContent = directionLabel(ai.direction);
    $('#ai-confidence').textContent = ai.confidence_pct == null ? '—' : `${n(ai.confidence_pct, 0)}%`;
    $('#ai-tested').textContent = Number(ai.calibration?.historical_tested_5d ?? d.history_evaluation?.by_horizon?.['5']?.tested ?? 0).toLocaleString('ja-JP');
    $('#ai-foot').textContent = ai.status === 'ok' ? `${ai.grade_label || 'AI検証済み'} · ${ai.model || 'モデル不明'}` : (ai.grade_label || 'Gemini検証が未実施です');
  }

  function chartBars() {
    const bars = state.data?.bars || [];
    if (state.chartDays === 'all') return bars;
    const count = Math.max(1, Math.min(Number(state.chartDays) || 180, bars.length));
    let start = Math.max(0, bars.length - count);
    const selectedIndex = bars.findIndex(bar => bar.date === state.selectedDate);
    if (selectedIndex >= 0 && (selectedIndex < start || selectedIndex >= start + count)) {
      start = Math.max(0, Math.min(bars.length - count, selectedIndex - Math.floor(count / 2)));
    }
    return bars.slice(start, start + count);
  }

  function renderChart() {
    const root = $('#price-chart');
    const bars = chartBars();
    if (!bars.length) {
      root.innerHTML = '<div class="empty-state">表示できるローソク足データがありません。</div>';
      return;
    }
    if (!bars.some(b => b.date === state.selectedDate)) state.selectedDate = bars[bars.length - 1].date;
    const width = Math.max(300, Math.floor(root.getBoundingClientRect().width || root.clientWidth || 800));
    const height = width < 540 ? 250 : width < 800 ? 280 : 325;
    const margin = { top: 17, right: 58, bottom: 29, left: 12 };
    const plotLeft = margin.left;
    const plotRight = width - margin.right;
    const plotTop = margin.top;
    const plotBottom = height - margin.bottom;
    const plotW = plotRight - plotLeft;
    const plotH = plotBottom - plotTop;
    const valid = bars.filter(b => Number.isFinite(Number(b.high)) && Number.isFinite(Number(b.low)));
    if (!valid.length) { root.innerHTML = '<div class="empty-state">価格データがありません。</div>'; return; }
    let min = Math.min(...valid.map(b => Number(b.low)));
    let max = Math.max(...valid.map(b => Number(b.high)));
    const extra = (max - min) * .08 || max * .02 || 1;
    min -= extra; max += extra;
    const y = value => plotBottom - ((Number(value) - min) / (max - min)) * plotH;
    const step = plotW / bars.length;
    const bodyW = Math.max(1.35, Math.min(8, step * .55));
    const selected = state.selectedDate;
    const yTicks = 5;
    const svgParts = [`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="TQQQの日足ローソク足チャート。ローソクを選ぶと日付別データを表示します。" xmlns="http://www.w3.org/2000/svg">`];
    for (let i = 0; i <= yTicks; i++) {
      const value = max - (max - min) * (i / yTicks);
      const yy = plotTop + plotH * (i / yTicks);
      svgParts.push(`<line x1="${plotLeft}" y1="${yy}" x2="${plotRight}" y2="${yy}" class="chart-grid-line"/>`);
      svgParts.push(`<text x="${width - 5}" y="${yy + 3.5}" text-anchor="end" class="chart-axis-label">${esc(value.toFixed(0))}</text>`);
    }
    const selectedIndex = bars.findIndex(b => b.date === selected);
    if (selectedIndex >= 0) {
      const xx = plotLeft + step * selectedIndex + step / 2;
      svgParts.push(`<rect x="${Math.max(plotLeft, xx - step / 2)}" y="${plotTop}" width="${Math.max(1, step)}" height="${plotH}" class="chart-select-band"/>`);
    }
    bars.forEach((bar, i) => {
      const open = Number(bar.open), high = Number(bar.high), low = Number(bar.low), close = Number(bar.close);
      if (![open, high, low, close].every(Number.isFinite)) return;
      const x = plotLeft + step * i + step / 2;
      const yo = y(open), yh = y(high), yl = y(low), yc = y(close);
      const up = close >= open;
      const color = up ? '#159575' : '#cf525d';
      const bodyY = Math.min(yo, yc);
      const bodyH = Math.max(1.2, Math.abs(yo-yc));
      const selectedClass = bar.date === selected ? ' selected' : '';
      const pats = (bar.patterns || []).map(p => patternLabel(p)).join('、') || '分類なし';
      const label = `${bar.date} / O ${open.toFixed(2)} H ${high.toFixed(2)} L ${low.toFixed(2)} C ${close.toFixed(2)} / ${pats}`;
      svgParts.push(`<g class="chart-candle${selectedClass}" data-date="${esc(bar.date)}" tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(label)}</title><line class="candle-wick" x1="${x.toFixed(2)}" y1="${yh.toFixed(2)}" x2="${x.toFixed(2)}" y2="${yl.toFixed(2)}" stroke="${color}" stroke-width="${Math.max(1, Math.min(2.4, step*.16)).toFixed(2)}"/><rect class="candle-body" x="${(x-bodyW/2).toFixed(2)}" y="${bodyY.toFixed(2)}" width="${bodyW.toFixed(2)}" height="${bodyH.toFixed(2)}" rx=".5" fill="${up ? '#cbeee2' : '#f8dfe1'}" stroke="${color}" stroke-width=".8"/></g>`);
    });
    const labelSlots = Math.min(7, Math.max(3, Math.floor(plotW / 100)));
    for (let i=0; i<labelSlots; i++) {
      const idx = Math.round(i * (bars.length - 1) / Math.max(1, labelSlots - 1));
      const xx = plotLeft + step * idx + step / 2;
      svgParts.push(`<text x="${xx}" y="${height-8}" text-anchor="middle" class="chart-axis-label">${esc(dateLabel(bars[idx]?.date, false))}</text>`);
    }
    const newsDates = new Set((state.history.news_archive || []).map(x => x.published_date).filter(Boolean));
    bars.forEach((bar, i) => {
      if (!newsDates.has(bar.date)) return;
      const x = plotLeft + step * i + step / 2;
      svgParts.push(`<circle cx="${x.toFixed(2)}" cy="${(plotTop+5).toFixed(2)}" r="${Math.min(2.8,Math.max(1.4,step*.16))}" fill="#8c87f3" opacity=".85"/>`);
    });
    svgParts.push('</svg>');
    root.innerHTML = svgParts.join('');
    root.querySelectorAll('.chart-candle').forEach(node => {
      node.addEventListener('click', () => selectDate(node.dataset.date));
      node.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectDate(node.dataset.date); }
      });
    });
    // When the candle bars are very narrow, plot-area clicks still select the nearest session.
    const svg = $('svg', root);
    svg?.addEventListener('click', event => {
      if (event.target.closest('.chart-candle')) return;
      const rect = svg.getBoundingClientRect();
      if (!rect.width) return;
      const scaleX = width / rect.width;
      const x = (event.clientX - rect.left) * scaleX;
      if (x < plotLeft || x > plotRight) return;
      const idx = Math.max(0, Math.min(bars.length - 1, Math.floor((x - plotLeft) / step)));
      selectDate(bars[idx].date);
    });
    $('#chart-date-range').textContent = `${dateLabel(bars[0].date)} — ${dateLabel(bars[bars.length-1].date)} · ${bars.length}本`;
    $('#chart-selected-label').textContent = `選択日：${dateLabel(state.selectedDate)}`;
  }

  function selectDate(date) {
    if (!date || !state.data?.bars?.length) return;
    const bars = state.data.bars;
    let exact = bars.find(item => item.date === date);
    if (!exact) {
      // If the user selects a weekend/holiday, show the most recent preceding trading session.
      exact = [...bars].reverse().find(item => item.date <= date) || bars[0];
      showToast(`${dateLabel(date)}は取引日データがないため、${dateLabel(exact.date)}を表示します。`);
    }
    state.selectedDate = exact.date;
    const jumpDate = $('#jump-date');
    if (jumpDate) jumpDate.value = exact.date;
    renderChart();
    renderSelectedDate();
  }

  function articlesForDate(date) {
    const output = new Map();
    const archive = new Map((state.history.news_archive || []).map(item => [item.id, item]));
    for (const item of state.history.news_archive || []) {
      if (item.published_date === date) output.set(item.id, { ...item, relation: '公開日が選択日と一致' });
    }
    const snapshot = (state.history.snapshots || []).find(item => item.as_of_date === date);
    if (snapshot) {
      for (const id of snapshot.source_ids || []) {
        const item = archive.get(id);
        if (item && !output.has(id)) output.set(id, { ...item, relation: `${dateLabel(date)}の分析時に収集` });
      }
      for (const item of snapshot.news || []) {
        if (item.id && !output.has(item.id)) output.set(item.id, { ...item, relation: `${dateLabel(date)}の分析時に収集` });
      }
    }
    return [...output.values()].sort((a,b) => String(b.published_at || '').localeCompare(String(a.published_at || ''))).slice(0, 12);
  }

  function renderSelectedDate() {
    const date = state.selectedDate;
    const bar = (state.data?.bars || []).find(item => item.date === date);
    if (!bar) return;
    $('#selected-date-title').textContent = dateLabel(bar.date);
    $('#selected-regime').textContent = regimeLabel(bar.regime);
    $('#selected-open').textContent = usd(bar.open);
    $('#selected-high').textContent = usd(bar.high);
    $('#selected-low').textContent = usd(bar.low);
    $('#selected-close').textContent = usd(bar.close);
    const patternsNode = $('#selected-patterns');
    const patterns = Array.isArray(bar.patterns) ? bar.patterns : [];
    patternsNode.innerHTML = patterns.length ? patterns.map(id => `<span class="pattern-tag ${['doji','spinning_top'].includes(id) ? 'neutral-tag' : ''}">${esc(patternLabel(id))}</span>`).join('') : '<span class="muted">分類できる足はありません</span>';
    const rows = [1,3,5,10].map(h => {
      const value = bar.forward_returns_pct?.[String(h)];
      return `<div class="forward-return-item"><span>${h}営業日後</span><strong class="${pctClass(value)}">${pct(value,2,true)}</strong></div>`;
    }).join('');
    $('#selected-forward-returns').innerHTML = rows;
    const events = articlesForDate(date);
    $('#selected-source-count').textContent = `${events.length}件`;
    $('#selected-date-events').innerHTML = events.length ? events.map(item => {
      const cats = (item.categories || []).slice(0,2).map(c => CATEGORY[c] || c).join(' / ');
      return `<article class="date-event">${linkHtml(item.url, item.title)}<div class="date-event-meta"><span>${esc(dateTimeLabel(item.published_at))}</span><span>${esc(item.source || '出典不明')}</span><span>${esc(item.relation || '')}</span>${cats ? `<span class="mini-category">${esc(cats)}</span>` : ''}</div></article>`;
    }).join('') : '<div class="empty-state compact">この日付に対応する保存済みニュースはありません。記事履歴はアプリ稼働開始後から蓄積されます。</div>';
    $('#chart-selected-label').textContent = `選択日：${dateLabel(bar.date)}`;
  }

  function statForPattern(pattern) { return pattern?.horizons?.[String(state.horizon)] || {}; }
  function renderPatternTable() {
    const all = state.data?.pattern_statistics || [];
    const current = new Set(state.data?.current_patterns || []);
    const q = state.search.trim().toLocaleLowerCase('ja-JP');
    const filtered = all.filter(p => {
      if (state.onlyCurrentPatterns && !current.has(p.id)) return false;
      if (q && !(p.label || '').toLocaleLowerCase('ja-JP').includes(q) && !(p.id || '').toLowerCase().includes(q)) return false;
      return true;
    }).sort((a,b) => (b.occurrences || 0) - (a.occurrences || 0) || String(a.label).localeCompare(String(b.label), 'ja'));
    $('#pattern-count').textContent = `${filtered.length}種類`;
    const body = $('#pattern-table-body');
    if (!filtered.length) {
      body.innerHTML = '<tr><td colspan="9" class="table-empty">条件に一致する足の種類はありません。</td></tr>';
      return;
    }
    body.innerHTML = filtered.map(p => {
      const s = statForPattern(p);
      const grade = s.grade || 'D';
      const freq = Number(p.frequency_pct || 0);
      const selected = state.selectedPattern === p.id ? ' active' : '';
      return `<tr class="pattern-row${selected}" data-pattern="${esc(p.id)}" tabindex="0" aria-label="${esc(p.label)}の詳細を表示"><td><span class="pattern-name-cell"><i class="pattern-name-mark"></i>${esc(p.label)}</span></td><td>${Number(p.occurrences || 0).toLocaleString('ja-JP')}</td><td><span class="frequency-cell"><span class="mini-bar"><i style="width:${Math.min(100,Math.max(0,freq))}%"></i></span>${pct(freq,1)}</span></td><td class="${pctClass(s.up_rate_pct)}">${pct(s.up_rate_pct,1)}</td><td class="${pctClass(s.down_rate_pct)}">${pct(s.down_rate_pct,1)}</td><td class="positive-text">${pct(s.avg_up_pct,2,true)}</td><td class="negative-text">${pct(s.avg_down_pct,2,true)}</td><td><span class="inline-grade ${gradeClass(grade)}">${esc(grade)}</span></td><td>${dateLabel(p.latest_date)}</td></tr>`;
    }).join('');
    $$('.pattern-row', body).forEach(row => {
      const open = () => { state.selectedPattern = row.dataset.pattern; renderPatternTable(); renderPatternDetail(); };
      row.addEventListener('click', open);
      row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });
  }

  function renderPatternDetail() {
    const root = $('#pattern-detail-card');
    const pattern = state.data?.pattern_statistics?.find(p => p.id === state.selectedPattern);
    if (!pattern) { root.innerHTML = '<div class="pattern-detail-empty">表の足の種類を選択すると、出現履歴と各期間の統計が表示されます。</div>'; return; }
    const horizonCards = [1,3,5,10].map(h => {
      const s = pattern.horizons?.[String(h)] || {};
      const hitLabel = s.expected_direction === 'neutral' ? '±0.5%以内' : '方向一致';
      return `<div class="horizon-stat"><span>${h}営業日後</span><strong class="${pctClass(s.avg_return_pct)}">${pct(s.avg_return_pct,2,true)}</strong><small>上昇 ${pct(s.up_rate_pct,1)} / 下落 ${pct(s.down_rate_pct,1)}</small><small>${hitLabel} ${pct(s.hit_rate_pct,1)} · n=${Number(s.n || 0)}</small><small>信頼度 ${esc(s.grade || 'D')}</small></div>`;
    }).join('');
    const occur = (pattern.recent_occurrences || []).slice(0, 14).map(o => {
      const val = o.forward_returns_pct?.[String(state.horizon)];
      return `<span class="occurrence-chip"><button type="button" data-select-date="${esc(o.date)}">${dateLabel(o.date)}</button><span class="return-value ${pctClass(val)}">${pct(val,2,true)}</span></span>`;
    }).join('');
    root.innerHTML = `<div class="pattern-detail-layout"><div class="pattern-detail-main"><span class="section-kicker">SELECTED CANDLE PATTERN</span><h3>${esc(pattern.label)} <span class="grade-badge ${gradeClass(pattern.horizons?.[String(state.horizon)]?.grade)}">${esc(pattern.horizons?.[String(state.horizon)]?.grade || 'D')}</span></h3><p>${esc(PATTERN_DESC[pattern.id] || '足の形状に基づく分類です。')}</p><p style="margin-top:7px">全履歴 ${Number(pattern.occurrences || 0).toLocaleString('ja-JP')}回 · 出現頻度 ${pct(pattern.frequency_pct,2)} · 最新出現 ${dateLabel(pattern.latest_date)}</p><div class="pattern-horizon-grid">${horizonCards}</div></div><div class="pattern-occurrences"><h4>最近の出現日と、その後の${state.horizon}営業日リターン</h4><div class="occurrence-list">${occur || '<span class="muted">確認できる出現履歴はありません。</span>'}</div><p class="muted" style="margin:12px 0 0;font-size:9px">未確定の未来リターンは「—」で表示。日付を選ぶとローソク足チャートも移動します。</p></div></div>`;
    $$('[data-select-date]', root).forEach(button => button.addEventListener('click', () => selectDate(button.dataset.selectDate)));
  }

  function renderNews() {
    const news = state.data?.news || [];
    $('#news-count').textContent = `${news.length}件`;
    const filter = state.category;
    const filtered = news.filter(item => filter === 'all' || (item.categories || []).includes(filter));
    $('#news-list').innerHTML = filtered.length ? filtered.map(item => {
      const cats = (item.categories || []).map(c => CATEGORY[c] || c).join(' / ') || 'カテゴリ未分類';
      const tone = ({ positive_keyword_signal: 'ポジティブ語を検出', negative_keyword_signal: 'ネガティブ語を検出', mixed_or_unknown: '方向は未判定' })[item.keyword_tone] || '方向は未判定';
      return `<article class="news-item">${linkHtml(item.url, item.title)}<div class="news-meta"><span>${esc(dateTimeLabel(item.published_at))}</span><span class="news-source">${esc(item.source || '出典不明')}</span><span>${esc(tone)}</span></div><div class="news-summary-line">分類タグ：${esc(cats)} · ${esc(item.tone_method || '見出しベースの自動分類')}</div></article>`;
    }).join('') : `<div class="empty-state compact">${news.length ? 'このカテゴリのニュース参照はありません。' : '取得できるニュース参照がありません。価格統計はニュース取得の成否とは独立して計算されます。'}</div>`;
  }

  function renderContext() {
    const d = state.data;
    const ctx = d.confidence?.context || {};
    const latestCats = [...new Set((d.news || []).flatMap(x => x.categories || []))];
    $('#regime-pill').textContent = regimeLabel(ctx.market_regime || d.quote?.market_regime);
    $('#regime-title').textContent = regimeLabel(ctx.market_regime || d.quote?.market_regime);
    $('#regime-description').textContent = REGIME[ctx.market_regime || d.quote?.market_regime]?.description || REGIME.neutral.description;
    $('#context-qqq').textContent = usd(d.quote?.qqq_close);
    $('#context-vix').textContent = n(d.quote?.vix_close, 2);
    $('#context-news-tags').textContent = latestCats.length ? latestCats.map(c => CATEGORY[c] || c).slice(0,3).join('、') : 'タグなし';
    $('#context-history-count').textContent = Number(state.history.snapshots?.length || 0).toLocaleString('ja-JP') + '日';
  }

  function renderAiReview() {
    const ai = state.data?.confidence?.gemini || {};
    $('#ai-detail-grade').className = `grade-badge ${gradeClass(ai.grade)}`;
    $('#ai-detail-grade').textContent = formatGrade(ai.grade);
    $('#ai-direction-heading').textContent = directionLabel(ai.direction);
    $('#ai-detail-summary').textContent = ai.summary || 'AI検証は実行されていません。';
    const list = (selector, items, empty) => {
      const root = $(selector);
      const clean = Array.isArray(items) ? items.filter(x => String(x).trim()) : [];
      root.innerHTML = clean.length ? clean.map(x => `<li>${esc(x)}</li>`).join('') : `<li>${esc(empty)}</li>`;
    };
    list('#ai-reasons', ai.reasons, '明示された根拠はありません。');
    list('#ai-risks', ai.risk_factors, ai.status === 'ok' ? '追加のリスク要因は明示されていません。' : 'Geminiが未検証のため、AI由来の要因はありません。');
    $('#ai-model-name').textContent = ai.model || '未設定';
    $('#ai-evaluated-at').textContent = dateTimeLabel(ai.evaluated_at);
    $('#model-chip').textContent = ai.status === 'ok' ? `AI検証済み · ${ai.model || 'Gemini'}` : ai.status === 'not_configured' ? 'Gemini API未設定' : 'AI検証なし';

    const sourceMap = new Map([...(state.history.news_archive || []), ...(state.data.news || []), ...(state.data.gemini_cited_sources || [])].map(item => [item.id, item]));
    const cited = (ai.cited_source_ids || []).map(id => sourceMap.get(id)).filter(Boolean);
    $('#ai-citation-count').textContent = `${cited.length}件`;
    $('#ai-citations').innerHTML = cited.length ? cited.map(item => `<article class="citation-item">${linkHtml(item.url, item.title)}<p>${esc(dateTimeLabel(item.published_at))} · ${esc(item.source || '出典不明')} · ID: ${esc(item.id)}</p></article>`).join('') : '<div class="empty-state compact">Geminiが紐付けた利用可能な出典はありません。出典のない文章は未検証の所見として扱ってください。</div>';
  }

  function renderValidation() {
    const evals = state.data?.history_evaluation || {};
    const by = evals.by_horizon || {};
    const five = by['5'] || {};
    $('#ai-accuracy').textContent = five.accuracy_pct == null ? '—' : pct(five.accuracy_pct, 1);
    $('#validation-table-body').innerHTML = [1,3,5,10].map(h => {
      const row = by[String(h)] || {};
      return `<tr><td>${h}営業日後</td><td>${Number(row.tested || 0).toLocaleString('ja-JP')}</td><td>${row.accuracy_pct == null ? '—' : pct(row.accuracy_pct,1)}</td><td class="${pctClass(row.mean_realized_return_pct)}">${pct(row.mean_realized_return_pct,2,true)}</td></tr>`;
    }).join('');
    const total = Number(evals.overall?.tested || 0);
    $('#validation-footnote').textContent = total ? `方向一致率の集計は${total}件の検証済みAI判定を含みます。中立判定は|実績リターン|が0.5%以内の場合を一致とします。` : '検証済みのAI判定はまだありません。実際の価格データを使い、予測後の必要営業日数が経過してから集計します。';
  }

  function closeSidebar() {
    $('#sidebar').classList.remove('open');
    $('#mobile-scrim').hidden = true;
    $('#menu-toggle').setAttribute('aria-expanded', 'false');
  }
  function bindEvents() {
    $('#refresh-button').addEventListener('click', () => loadData({ announce: true }));
    $('#chart-range').addEventListener('change', event => {
      state.chartDays = event.target.value === 'all' ? 'all' : Math.max(30, Math.min(10000, Number(event.target.value) || 180));
      renderChart();
      renderSelectedDate();
    });
    $('#jump-date-button').addEventListener('click', () => {
      const value = $('#jump-date').value;
      if (!value) { showToast('移動する日付を選択してください。'); return; }
      if (value < (state.data?.data_range?.start || '') || value > (state.data?.data_range?.end || '')) {
        showToast('指定した日付は取得済みデータの範囲外です。'); return;
      }
      selectDate(value);
    });
    $('#jump-date').addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); $('#jump-date-button').click(); }
    });
    $('#horizon-select').addEventListener('change', event => {
      state.horizon = [1,3,5,10].includes(Number(event.target.value)) ? Number(event.target.value) : 5;
      renderPatternTable();
      renderPatternDetail();
    });
    $('#pattern-search').addEventListener('input', event => { state.search = event.target.value || ''; renderPatternTable(); });
    $('#only-current-patterns').addEventListener('change', event => { state.onlyCurrentPatterns = Boolean(event.target.checked); renderPatternTable(); });
    $('#event-category-filter').addEventListener('change', event => { state.category = event.target.value || 'all'; renderNews(); });
    $('#menu-toggle').addEventListener('click', () => {
      const open = !$('#sidebar').classList.contains('open');
      $('#sidebar').classList.toggle('open', open);
      $('#mobile-scrim').hidden = !open;
      $('#menu-toggle').setAttribute('aria-expanded', String(open));
    });
    $('#mobile-scrim').addEventListener('click', closeSidebar);
    $$('.nav-link').forEach(link => link.addEventListener('click', () => {
      $$('.nav-link').forEach(item => item.classList.toggle('active', item === link));
      if (window.matchMedia('(max-width: 767px)').matches) closeSidebar();
    }));
    let resizeTimer;
    window.addEventListener('resize', () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => { if (state.data) renderChart(); }, 100);
    }, { passive: true });
  }

  bindEvents();
  loadData();
})();
