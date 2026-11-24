/* Optional author-initiated browser speech input, with keyboard fallback.
 * SpeechRecognition MAY route audio to a browser vendor's service: warn
 * before starting. ACHEWIW itself does not upload the audio or auto-submit.
 */
(() => {
  'use strict';
  const Engine = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Engine) return;
  const selectors = [
    'textarea[data-field]',
    'textarea[data-scene-field]',
    'textarea[data-plan-goal]',
    '#storyText',
    '#polishInstructions',
    '#memoryNodeStatement',
    '#paragraphText'
  ].join(',');
  let active = null;
  let consented = false;
  const buttons = new WeakMap();

  function stopCurrent() {
    if (active) {
      try { active.recognition.stop(); } catch (_) {}
    }
  }

  function insertRecognized(field, raw) {
    const text = String(raw || '').trim();
    if (!text) return;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    const prefix = start && !/\s$/.test(field.value.slice(0, start)) ? ' ' : '';
    const suffix = end < field.value.length && !/^\s/.test(field.value.slice(end)) ? ' ' : '';
    const replacement = prefix + text + suffix;
    if (field.maxLength >= 0 &&
        field.value.length - (end - start) + replacement.length > field.maxLength) {
      field.setCustomValidity('Диктовка превышает лимит этого поля');
      field.reportValidity();
      return;
    }
    field.setCustomValidity('');
    field.setRangeText(replacement, start, end, 'end');
    // Existing Studio/DOCWORK handlers receive the exact same input event
    // as typing. Saving/accepting changes is still the author's decision.
    field.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function addButton(field) {
    if (!(field instanceof HTMLTextAreaElement) || buttons.has(field)) return;
    if (field.readOnly || field.disabled) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '🎙 Надиктовать';
    button.title = 'Диктовка в это поле. Не сохраняет и не отправляет книгу автоматически.';
    button.setAttribute('aria-label', 'Надиктовать текст в выбранное поле');
    button.className = 'achewiw-dictation-action';
    button.style.cssText = 'display:inline-flex;align-items:center;gap:.3rem;'+
      'margin:.35rem 0 .65rem;padding:.35rem .65rem;font-size:.82rem;'+
      'border-radius:.5rem;cursor:pointer;';
    button.addEventListener('click', () => {
      if (active && active.field === field) {
        stopCurrent();
        return;
      }
      if (active) stopCurrent();
      if (!consented) {
        const ok = window.confirm(
          'Для диктовки используется распознавание речи браузера. '+
          'В зависимости от браузера звук может обрабатываться внешним сервисом. '+
          'ACHEWIW сам аудио не загружает. Разрешить диктовку в это поле?'
        );
        if (!ok) return;
        consented = true;
      }
      const recognition = new Engine();
      const session = { recognition, field, button, handled: new Set() };
      active = session;
      recognition.lang = 'ru-RU';
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onstart = () => {
        button.textContent = '⏹ Остановить диктовку';
        button.setAttribute('aria-pressed', 'true');
      };
      recognition.onresult = (event) => {
        if (active !== session) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const part = event.results[i];
          if (!part.isFinal || session.handled.has(i)) continue;
          session.handled.add(i);
          insertRecognized(field, part[0]?.transcript || '');
        }
      };
      recognition.onerror = (event) => {
        button.title = 'Распознавание: ' + String(event.error || 'ошибка');
      };
      recognition.onend = () => {
        button.textContent = '🎙 Надиктовать';
        button.setAttribute('aria-pressed', 'false');
        if (active === session) active = null;
      };
      try { recognition.start(); } catch (error) {
        if (active === session) active = null;
        button.textContent = '🎙 Надиктовать';
        button.title = 'Не удалось включить микрофон: ' + String(error.message || error);
      }
    });
    field.insertAdjacentElement('afterend', button);
    buttons.set(field, button);
  }

  function scan(root) {
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    if (root.matches?.(selectors)) addButton(root);
    root.querySelectorAll?.(selectors).forEach(addButton);
  }

  function ready() {
    scan(document);
    const observer = new MutationObserver((mutations) => {
      for (const record of mutations) {
        for (const node of record.addedNodes) scan(node);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('pagehide', stopCurrent);
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', ready, { once: true });
  else ready();
})();
