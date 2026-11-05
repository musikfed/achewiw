/* Read-only diagnostics. Never sends commands to Ollama, Docker or the host. */
(() => {
  const byId = id => document.getElementById(id);
  const show = (id, value) => { const el = byId(id); if (el) el.textContent = value; };
  const fmtPct = value => Number.isFinite(value) ? value.toFixed(1) + '%' : 'Нет данных';
  const bytesGiB = value => Number.isFinite(value) && value >= 0
    ? (value / 1024 ** 3).toFixed(1) + ' ГиБ' : 'н/д';
  let working = false;
  const modelSelector = byId('model');

  function render(payload) {
    const host = payload?.host || {};
    const ollama = payload?.ollama || {};
    if (host.status === 'ok') {
      show('monitorSummary', 'Данные хоста обновлены ' + (host.age_seconds ?? '?') + ' сек. назад');
      show('monitorCpu', fmtPct(host.cpu?.usage_percent));
      show('monitorCpuDetail', 'Логических CPU: ' + (host.cpu?.logical_processors ?? 'н/д') +
        ' · load 1 мин: ' + (host.cpu?.load_1 ?? 'н/д'));
      show('monitorRam', fmtPct(host.memory?.usage_percent));
      show('monitorRamDetail', bytesGiB(host.memory?.used_bytes) + ' / ' +
        bytesGiB(host.memory?.total_bytes));
      if (host.gpu_status === 'ok' && Array.isArray(host.gpus) && host.gpus.length) {
        show('monitorGpu', host.gpus.map(g => fmtPct(g.utilization_percent)).join(' / '));
        show('monitorGpuDetail', host.gpus.map(g => g.name + ': ' +
          bytesGiB(g.memory_used_bytes) + ' / ' + bytesGiB(g.memory_total_bytes) +
          (Number.isFinite(g.temperature_c) ? ' · ' + g.temperature_c + '°C' : '')).join(' | '));
      } else {
        show('monitorGpu', 'Нет данных');
        show('monitorGpuDetail', 'nvidia-smi недоступен на хосте');
      }
    } else {
      show('monitorSummary', host.status === 'stale'
        ? 'Сборщик не обновлял показания более 30 секунд'
        : 'Нет свежих показаний хоста — сборщик ещё не включён');
      for (const id of ['monitorCpu', 'monitorRam', 'monitorGpu']) show(id, 'Нет данных');
      show('monitorCpuDetail', 'Данные не поступают');
      show('monitorRamDetail', 'Данные не поступают');
      show('monitorGpuDetail', 'Неизвестно, занята ли видеопамять');
    }

    const selected = modelSelector?.value || 'не выбрана';
    const configured = ollama.configured_default_model || 'не настроена';
    show('monitorModel', selected);
    if (ollama.status !== 'ok') {
      show('monitorModelDetail', 'Ollama недоступна · модель по умолчанию: ' + configured);
    } else {
      const loaded = (ollama.loaded_models || []).map(item => item.name +
        (Number.isFinite(item.vram_bytes) ? ' (' + bytesGiB(item.vram_bytes) + ' VRAM)' : ''));
      show('monitorModelDetail', 'Загружены: ' + (loaded.length ? loaded.join(', ') : 'нет') +
        ' · по умолчанию: ' + configured);
    }
  }

  async function refreshMonitor() {
    if (working || document.hidden) return;
    working = true;
    const button = byId('monitorRefresh');
    if (button) button.disabled = true;
    try {
      const response = await fetch('/api/monitoring', {
        cache: 'no-store',
        headers: { 'Accept': 'application/json' },
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      render(await response.json());
    } catch (error) {
      show('monitorSummary', 'Мониторинг недоступен: ' + error.message);
      for (const id of ['monitorCpu', 'monitorRam', 'monitorGpu']) show(id, 'Нет данных');
      show('monitorModel', modelSelector?.value || 'Нет данных');
      show('monitorModelDetail', 'Нет связи с сервером мониторинга');
    } finally {
      working = false;
      if (button) button.disabled = false;
    }
  }

  const shell = document.querySelector('.host-monitor');
  const toggle = byId('monitorToggle');
  function setCollapsed(collapsed) {
    if (!shell || !toggle) return;
    shell.classList.toggle('monitor-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.textContent = collapsed ? 'Развернуть показатели' : 'Свернуть';
    try { localStorage.setItem('achewiw.monitorCollapsed', collapsed ? '1' : '0'); } catch {}
  }
  if (shell && toggle) {
    let collapsed = false;
    try { collapsed = localStorage.getItem('achewiw.monitorCollapsed') === '1'; } catch {}
    setCollapsed(collapsed);
    toggle.addEventListener('click', () => {
      setCollapsed(!shell.classList.contains('monitor-collapsed'));
    });
  }

  byId('monitorRefresh')?.addEventListener('click', refreshMonitor);
  modelSelector?.addEventListener('change', refreshMonitor);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshMonitor();
  });
  refreshMonitor();
  setInterval(refreshMonitor, 10_000);
})();
