/* ACHEWIW illustration studio: real ComfyUI; optional author-reviewed Ollama
   image prompt polish. Never silently allow concurrent ACHEWIW GPU jobs. */
(() => {
  const el = id => document.getElementById(id);
  const current = () => typeof project === 'object' ? project : null;
  let imageBusy = false;
  let engineControlBusy = false;
  let controlStatus = null;
  let activeProjectId = null;

  async function json(url, options = {}) {
    const res = await fetch(url, {cache:'no-store', ...options});
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : 'HTTP ' + res.status);
    }
    return res.status === 204 ? null : await res.json();
  }
  const draftKey = id => 'achewiw.v8.illustrationDraft.' + id;
  const prefKey = 'achewiw.v8.illustrationPref.';
  function setStatus(message) {
    el('illustrationStepStatus').textContent = message;
  }
  function saveDraft() {
    const p = current();
    if (!p) return;
    const saved = {
      scene_id: el('illustrationScene').value,
      caption: el('illustrationCaption').value,
      placement: el('illustrationPlacement').value,
      description: el('illustrationPrompt').value,
      prepared: el('illustrationPreparedPrompt').value
    };
    try { sessionStorage.setItem(draftKey(p.id), JSON.stringify(saved)); } catch {}
    try {
      localStorage.setItem(prefKey + 'releaseLLM',
        el('illustrationReleaseLLM').checked ? '1' : '0');
      localStorage.setItem(prefKey + 'releaseComfy',
        el('illustrationReleaseComfy').checked ? '1' : '0');
    } catch {}
  }
  function storedDraft(id) {
    try {return JSON.parse(sessionStorage.getItem(draftKey(id)) || 'null') || {};}
    catch {return {};}
  }
  function restoreDraft(id) {
    const data = storedDraft(id);
    el('illustrationCaption').value = data.caption || '';
    el('illustrationPlacement').value = ['start','end'].includes(data.placement) ?
      data.placement : 'start';
    el('illustrationPrompt').value = data.description || '';
    el('illustrationPreparedPrompt').value = data.prepared || '';
    try {
      el('illustrationReleaseLLM').checked =
        localStorage.getItem(prefKey + 'releaseLLM') === '1';
      el('illustrationReleaseComfy').checked =
        localStorage.getItem(prefKey + 'releaseComfy') === '1';
    } catch {}
    return data.scene_id;
  }

  function imageCard(projectId, row, chapters) {
    const card = document.createElement('article');
    card.className = 'illustration-card';
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.src = '/api/projects/' + encodeURIComponent(projectId) +
      '/illustrations/' + encodeURIComponent(row.id) + '.png';
    img.alt = row.caption || 'Иллюстрация';
    img.loading = 'lazy';
    const cap = document.createElement('figcaption');
    cap.textContent = row.caption || 'Без подписи';
    figure.append(img,cap);
    const meta = document.createElement('div');
    meta.className = 'illustration-meta';
    const title = document.createElement('p');
    title.textContent = (chapters.find(x=>x.id===row.scene_id)?.title||'Глава') +
      ' · ' + (row.placement==='end'?'после текста':'после заголовка');
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'danger-action';
    remove.textContent = 'Удалить изображение';
    remove.onclick = async () => {
      if(!confirm('Удалить именно этот рисунок из текущей книги?')) return;
      try {
        await json('/api/projects/'+projectId+'/illustrations/'+row.id,{method:'DELETE'});
        await refresh();
      } catch(e) {el('illustrationEngineStatus').textContent =
        'Удаление не выполнено: '+e.message;}
    };
    meta.append(title,remove);
    card.append(figure,meta);
    return card;
  }

  function renderEngineControls(engine, control) {
    const prepare = el('illustrationEnginePrepare');
    const stop = el('illustrationEngineStop');
    if (!control) {
      prepare.disabled = true;
      prepare.textContent = 'Контроллер не подключён';
      stop.disabled = true;
      el('illustrationEngineStatus').textContent = engine.available ?
        'ComfyUI отвечает, но кнопочное управление ещё не установлено.' :
        'Нет связи с ComfyUI и host-control. Обнови ACHEWIW с GitHub.';
      return;
    }
    if (control.busy) {
      prepare.disabled = true;
      prepare.textContent = 'Выполняется: ' + (control.active_action || 'ComfyUI');
      stop.disabled = true;
      el('illustrationEngineStatus').textContent =
        'Выполняется команда ComfyUI. Можно обновить статус, не запуская вторую команду.';
      return;
    }
    if (!control.installed) prepare.textContent = 'Установить ComfyUI';
    else if (!control.model_present) prepare.textContent = 'Загрузить SDXL (~7 ГБ)';
    else if (!control.api_ready) prepare.textContent = 'Запустить ComfyUI';
    else prepare.textContent = 'ComfyUI запущен';
    const foreignPort = control.port_busy && !control.owned_running && !control.api_ready;
    prepare.disabled = engineControlBusy || Boolean(control.api_ready && engine.available) ||
      Boolean(foreignPort) || Boolean(control.owned_running && !control.api_ready);
    stop.disabled = engineControlBusy || !control.owned_running;
    if (engine.available && control.api_ready) {
      el('illustrationEngineStatus').textContent = control.owned_running ?
        '● ComfyUI готов · SDXL доступен · сервер ACHEWIW' :
        '● ComfyUI отвечает · процесс принадлежит другому владельцу; не останавливаем его';
    } else if (foreignPort) {
      el('illustrationEngineStatus').textContent =
        'Порт 8188 занят неидентифицированным процессом. ACHEWIW его не трогает.';
    } else if (!control.installed) {
      el('illustrationEngineStatus').textContent =
        'ComfyUI не установлен. Нажми «Установить ComfyUI» для изолированной установки.';
    } else if (!control.model_present) {
      el('illustrationEngineStatus').textContent =
        'SDXL checkpoint отсутствует. Нажми кнопку загрузки; файл около 7 ГБ.';
    } else if (control.owned_running && !control.api_ready) {
      el('illustrationEngineStatus').textContent =
        'Наш процесс ComfyUI найден, но API не отвечает. Останови его и запусти снова.';
    } else {
      el('illustrationEngineStatus').textContent =
        'ComfyUI остановлен · SDXL установлен. Нажми «Запустить ComfyUI».';
    }
  }

  async function runControlJob(action, label) {
    const started = await json('/api/illustration-engine/control', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action})
    });
    if (!started.job_id) throw new Error('Контроллер не вернул идентификатор задачи');
    while (true) {
      const job = await json('/api/illustration-engine/control/jobs/' +
        encodeURIComponent(started.job_id));
      const tail = (job.output || '').split(/[\r\n]+/).filter(Boolean).slice(-3).join(' · ');
      setStatus(tail ? label + ' ' + tail : label);
      if (job.status === 'succeeded') return job;
      if (job.status === 'failed') throw new Error(tail || 'Команда ' + action + ' завершилась с ошибкой');
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  }

  async function prepareEngine() {
    if (engineControlBusy) return;
    engineControlBusy = true;
    renderEngineControls({available:false}, controlStatus);
    try {
      let state = await json('/api/illustration-engine/control');
      controlStatus = state;
      if (state.busy) throw new Error('Уже выполняется команда ComfyUI');
      if (!state.installed) {
        if (!confirm('Установить ComfyUI в отдельный каталог ACHEWIW? Будут загружены Python-зависимости и PyTorch CUDA 12.4.')) return;
        await runControlJob('install', 'Устанавливаем ComfyUI в отдельное окружение…');
      }
      state = await json('/api/illustration-engine/control');
      controlStatus = state;
      if (!state.model_present) {
        if (!confirm('Загрузить официальный SDXL Base checkpoint (~7 ГБ) в каталог ACHEWIW? Загрузка выполняется только после этого подтверждения.')) return;
        await runControlJob('model-sdxl', 'Загружаем SDXL (~7 ГБ), проверяем SHA-256…');
      }
      state = await json('/api/illustration-engine/control');
      controlStatus = state;
      if (state.port_busy && !state.owned_running && !state.api_ready) {
        throw new Error('Порт 8188 занят чужим процессом. Его не останавливаем.');
      }
      if (!state.api_ready) {
        await runControlJob('start', 'Запускаем ComfyUI только на localhost:8188…');
      }
      setStatus('ComfyUI запущен. SDXL будет загружен в VRAM при первой генерации.');
    } catch (err) {
      setStatus('Не удалось подготовить генератор: ' + err.message);
    } finally {
      engineControlBusy = false;
      await refresh();
    }
  }

  async function stopEngine() {
    if (engineControlBusy || !controlStatus?.owned_running) return;
    if (!confirm('Остановить только подтверждённый процесс ComfyUI ACHEWIW? Другие процессы и Ollama не затрагиваются.')) return;
    engineControlBusy = true;
    try {
      await runControlJob('stop', 'Останавливаем только собственный ComfyUI…');
      setStatus('ComfyUI остановлен. Общая Ollama и другие проекты не затронуты.');
    } catch (err) {
      setStatus('Не удалось остановить ComfyUI: ' + err.message);
    } finally {
      engineControlBusy = false;
      await refresh();
    }
  }

  async function refresh() {
    const p = current();
    if (!p) return;
    const projectId = p.id;
    const firstLoad = activeProjectId !== projectId;
    activeProjectId = projectId;
    const selected = firstLoad ? restoreDraft(projectId) :
      el('illustrationScene').value;
    const grid=el('illustrationGrid'),select=el('illustrationScene');
    select.replaceChildren();
    for(const chapter of p.scenes) {
      const opt = document.createElement('option');
      opt.value=chapter.id;opt.textContent=chapter.title;select.append(opt);
    }
    if (p.scenes.some(x=>x.id===selected)) select.value=selected;
    el('illustrationUpload').disabled = !p.scenes.length || imageBusy;
    el('illustrationGenerate').disabled = true;
    el('illustrationRefine').disabled = imageBusy;
    grid.textContent = p.scenes.length ? 'Загружаем иллюстрации…' :
      'Сначала добавь главу в разделе «02 · План».';
    try {
      const [items,engine,control]=await Promise.all([
        p.scenes.length ? json('/api/projects/'+projectId+'/illustrations') : Promise.resolve([]),
        json('/api/illustration-engine').catch(err=>({available:false,reason:err.message})),
        json('/api/illustration-engine/control').catch(()=>null),
      ]);
      if(!current() || current().id!==projectId) return;
      controlStatus=control;
      renderEngineControls(engine,control);
      if(!p.scenes.length) return;
      grid.replaceChildren();
      if(!items.length)grid.textContent=
        'Изображений пока нет. Выбери главу, загрузи PNG/JPEG/WebP или создай рисунок.';
      for(const item of items)grid.append(imageCard(projectId,item,p.scenes));
      el('illustrationGenerate').disabled=!engine.available||imageBusy;
    }catch(err){
      grid.textContent='Ошибка загрузки: '+err.message;
      el('illustrationEngineStatus').textContent='Не удалось проверить ComfyUI';
    }
  }
  async function upload() {
    const p=current(), file=el('illustrationFile').files[0];
    if(!p) return;
    if(!file){alert('Выбери PNG, JPEG или WebP');return;}
    const body=new FormData();
    body.set('file',file,file.name);
    body.set('scene_id',el('illustrationScene').value);
    body.set('caption',el('illustrationCaption').value);
    body.set('placement',el('illustrationPlacement').value);
    el('illustrationUpload').disabled=true;
    saveDraft();
    try{
      await json('/api/projects/'+p.id+'/illustrations',{method:'POST',body});
      el('illustrationFile').value='';
      await refresh();
    }catch(err){alert('Не удалось добавить изображение: '+err.message)}
    finally{el('illustrationUpload').disabled=false}
  }

  async function refine() {
    const p=current();
    if(!p||imageBusy) return;
    const description=el('illustrationPrompt').value.trim();
    if(description.length<3){alert('Опиши изображение хотя бы несколькими словами');return;}
    const chosen=document.getElementById('model')?.value;
    if(!chosen){alert('Выбери установленную текстовую модель Ollama');return;}
    if(!confirm('Текстовая модель '+chosen+' подготовит английский промпт для SDXL.\n'+
      'После ответа Ollama получит keep_alive=0 на выгрузку именно этой модели.\n'+
      'Её могут использовать другие приложения. Продолжить?'))return;
    imageBusy=true;el('illustrationRefine').disabled=true;
    el('illustrationGenerate').disabled=true;
    setStatus('1/2 · Ollama переводит и уточняет описание. ComfyUI не запущен.');
    saveDraft();
    try{
      const prepared=await json('/api/illustration-prompt/refine',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({description,model:chosen,confirm_shared_model:true})
      });
      if(!current()||current().id!==p.id)return;
      el('illustrationPreparedPrompt').value=prepared.prepared_prompt;
      saveDraft();
      setStatus('2/2 · Промпт готов. Проверь его и при необходимости исправь; '+
        'только затем запускай ComfyUI. Выбранная LLM получила запрос на выгрузку.');
      el('illustrationEngineStatus').textContent=
        'Промпт подготовлен через '+chosen+'; модель выгружается по keep_alive=0.';
    }catch(err){
      setStatus('Уточнение не удалось: '+err.message+
        '. Можно использовать исходное описание без Ollama.');
    }finally{
      imageBusy=false;el('illustrationRefine').disabled=false;
      await refresh();
    }
  }

  async function generate() {
    const p=current();
    if(!p||imageBusy)return;
    const plain=el('illustrationPrompt').value.trim();
    const prepared=el('illustrationPreparedPrompt').value.trim();
    const prompt=prepared||plain;
    if(!prompt){alert('Сначала опиши композицию рисунка');return;}
    if(prompt.length>2500){alert('Описание должно быть не длиннее 2500 символов');return;}
    const chosen=document.getElementById('model')?.value;
    const body={
      scene_id:el('illustrationScene').value,prompt,
      caption:el('illustrationCaption').value,
      placement:el('illustrationPlacement').value,
      release_comfy_vram:el('illustrationReleaseComfy').checked
    };
    imageBusy=true;el('illustrationGenerate').disabled=true;
    el('illustrationRefine').disabled=true;
    saveDraft();
    try{
      if(el('illustrationReleaseLLM').checked){
        if(!chosen)throw Error('Сначала выбери модель Ollama');
        if(!confirm('Выгрузить '+chosen+' из общей Ollama перед генерацией?\n'+
          'Это может повлиять на другие приложения, использующие ту же модель.'))return;
        setStatus('1/3 · Запрос на выгрузку выбранной Ollama-модели…');
        await json('/api/models/unload',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({model:chosen,confirm_shared_model:true})
        });
      }
      setStatus('2/3 · Проверяем занятость Ollama, затем ComfyUI рисует. '+
        'Одновременный запуск тяжёлых задач ACHEWIW запрещён.');
      const result=await json('/api/projects/'+p.id+'/illustrations/generate',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)
      });
      const releaseText={
        free_requested:'кэш ComfyUI освобождён',
        skipped_comfyui_queue_busy:'кэш не трогали: очередь ComfyUI занята',
        free_request_failed:'не удалось подтвердить освобождение кэша',
        not_requested:'освобождение кэша не запрашивалось'
      }[result.comfy_vram]||result.comfy_vram;
      setStatus('3/3 · Рисунок сохранён · '+releaseText+'. Ollama без запроса не трогали.');
      el('illustrationEngineStatus').textContent='Иллюстрация готова: '+result.checkpoint;
      alert('Изображение сохранено в книге. Модель: '+result.checkpoint);
    }catch(err){
      setStatus('Генерация остановлена: '+err.message+
        '. Никаких общих GPU-процессов не завершаем.');
    }finally{
      imageBusy=false;await refresh();
    }
  }
  for(const id of ['illustrationScene','illustrationCaption',
                    'illustrationPlacement','illustrationPrompt',
                    'illustrationPreparedPrompt','illustrationReleaseLLM',
                    'illustrationReleaseComfy']){
    const input=el(id);
    input.addEventListener('input',()=>{
      if(id==='illustrationPrompt'&&el('illustrationPreparedPrompt').value){
        el('illustrationPreparedPrompt').value='';
        setStatus('Исходное описание изменено: старый уточнённый промпт сброшен.');
      }
      saveDraft();
    });
    input.addEventListener('change',saveDraft);
  }
  el('illustrationUpload').addEventListener('click',upload);
  el('illustrationRefine').addEventListener('click',refine);
  el('illustrationGenerate').addEventListener('click',generate);
  el('illustrationEnginePrepare').addEventListener('click',prepareEngine);
  el('illustrationEngineStop').addEventListener('click',stopEngine);
  el('illustrationEngineRefresh').addEventListener('click',refresh);
  document.addEventListener('achewiw:tab-shown',event=>{
    if(event.detail?.name==='illustrations') refresh();
  });
  el('openIllustrations').addEventListener('click',()=>{
    if(!current())return;
    showTab('illustrations');
    el('illustrations').scrollIntoView({behavior:'smooth',block:'start'});
  });
  el('openTypography').addEventListener('click',()=>{
    if(!current())return;
    showTab('book');
    el('book').scrollIntoView({behavior:'smooth',block:'start'});
  });
})();
