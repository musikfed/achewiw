/* ACHEWIW illustration studio: real ComfyUI; optional author-reviewed Ollama
   image prompt polish. Never silently allow concurrent ACHEWIW GPU jobs. */
(() => {
  const el = id => document.getElementById(id);
  const current = () => typeof project === 'object' ? project : null;
  const COVER_ID = '00000000000000000000000000000000';
  let imageBusy = false;
  let engineControlBusy = false;
  let controlStatus = null;
  let watchedControlJob = null;
  const controlJobKey = 'achewiw.v9.comfyControlJob';
  let activeProjectId = null;
  let puzzleArtwork = [];
  let puzzleChapters = [];

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
      paragraph_index: Number(el('illustrationParagraphIndex').value)||0,
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
    el('illustrationPlacement').value = ['start','end','paragraph','cover'].includes(data.placement) ?
      data.placement : 'start';
    el('illustrationParagraphIndex').value=Number(data.paragraph_index)||1;
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
    title.textContent = (row.placement==='cover'?'Обложка':(chapters.find(x=>x.id===row.scene_id)?.title||'Глава')) +
      ' · ' + ({cover:'титульная обложка',end:'после текста',paragraph:'после абзаца № '+row.paragraph_index,start:'после заголовка'}[row.placement]||'в главе');
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
    if(row.placement!=='cover'){
      const clue=document.createElement('label');clue.className='puzzle-illustration-toggle';
      const check=document.createElement('input');check.type='checkbox';
      check.setAttribute('aria-label','Использовать рисунок как визуальную загадку');
      const span=document.createElement('span');span.textContent='Визуальная загадка с будущей разгадкой';
      clue.append(check,span);
      check.addEventListener('change',()=>{
        if(!check.checked)return;
        el('puzzleIllustration').value=row.id;
        syncPuzzleChapters();
        el('puzzleTitle').focus();
        el('puzzleStatus').textContent='Выбран рисунок главы. Укажи, где читатель узнает ответ.';
        el('puzzleTitle').scrollIntoView({behavior:'smooth',block:'center'});
      });
      meta.append(clue);
    }
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

  function savedControlJob() {
    try {
      const row = JSON.parse(sessionStorage.getItem(controlJobKey) || 'null');
      return row && /^[0-9a-f]{32}$/.test(row.job_id) ? row : null;
    } catch { return null; }
  }

  function saveControlJob(job_id, action) {
    try { sessionStorage.setItem(controlJobKey, JSON.stringify({job_id, action})); } catch {}
  }

  function clearControlJob(job_id) {
    try {
      if (savedControlJob()?.job_id === job_id) sessionStorage.removeItem(controlJobKey);
    } catch {}
  }

  function watchControlJob(job_id, action, label) {
    if (watchedControlJob?.job_id === job_id) return watchedControlJob.promise;
    const promise = (async () => {
      while (true) {
        const job = await json('/api/illustration-engine/control/jobs/' +
          encodeURIComponent(job_id));
        const tail = (job.output || '').split(/[\r\n]+/).filter(Boolean).slice(-3).join(' · ');
        setStatus(tail ? label + ' ' + tail : label);
        if (job.status === 'succeeded') return job;
        if (job.status === 'failed') {
          throw new Error(tail || 'Команда ' + action + ' завершилась с ошибкой');
        }
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
    })();
    watchedControlJob = {job_id, promise};
    return promise.finally(async () => {
      if (watchedControlJob?.job_id === job_id) watchedControlJob = null;
      clearControlJob(job_id);
      await refresh();
    });
  }

  function resumeControlJob(control) {
    if (!control) return;
    const persisted = savedControlJob();
    const job_id = control.active_job_id || persisted?.job_id;
    if (!job_id || watchedControlJob?.job_id === job_id) return;
    const action = control.active_action || persisted?.action || 'ComfyUI';
    saveControlJob(job_id, action);
    watchControlJob(job_id, action, 'Команда ComfyUI (' + action + ') выполняется…')
      .then(job => setStatus('Команда ComfyUI (' + job.action + ') завершена.'))
      .catch(err => setStatus('Команда ComfyUI (' + action + '): ' + err.message));
  }

  async function runControlJob(action, label) {
    const started = await json('/api/illustration-engine/control', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action})
    });
    if (!started.job_id) throw new Error('Контроллер не вернул идентификатор задачи');
    saveControlJob(started.job_id, action);
    return await watchControlJob(started.job_id, action, label);
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

  function syncPlacement(){
    const isCover=el('illustrationScene').value===COVER_ID;
    const position=el('illustrationPlacement');
    if(isCover){position.value='cover';position.disabled=true;}
    else{position.disabled=false;if(position.value==='cover')position.value='start';}
    el('illustrationParagraphWrap').hidden=isCover||position.value!=='paragraph';
    if(!isCover&&position.value==='paragraph'&&!Number(el('illustrationParagraphIndex').value))
      el('illustrationParagraphIndex').value='1';
  }
  function imageParams(){
    syncPlacement();
    const placement=el('illustrationPlacement').value;
    const paragraph_index=placement==='paragraph'?Number(el('illustrationParagraphIndex').value):0;
    if(placement==='paragraph'&&(!Number.isInteger(paragraph_index)||
       paragraph_index<1||paragraph_index>5000))
       throw Error('Номер абзаца должен быть от 1 до 5000');
    return {placement,paragraph_index};
  }
  function syncPuzzleChapters(){
    const selected=puzzleArtwork.find(x=>x.id===el('puzzleIllustration').value);
    const from=puzzleChapters.findIndex(x=>x.id===selected?.scene_id);
    const select=el('puzzleAnswerScene'),old=select.value;
    select.replaceChildren();
    for(let i=0;i<puzzleChapters.length;i++){
      if(i<=from||from<0)continue;
      const opt=document.createElement('option');opt.value=puzzleChapters[i].id;
      opt.textContent=(i+1)+'. '+puzzleChapters[i].title;
      select.append(opt);
    }
    if([...select.options].some(x=>x.value===old))select.value=old;
    if(!select.options.length)el('puzzleStatus').textContent=
      'Для загадки нужна будущая глава после главы-подсказки.';
  }
  async function loadPuzzles(){
    const p=current();if(!p)return;
    const id=p.id;
    try{
      const [images,items]=await Promise.all([
        json('/api/projects/'+id+'/illustrations'),
        json('/api/projects/'+id+'/visual-puzzles'),
      ]);
      if(!current()||current().id!==id)return;
      puzzleArtwork=images.filter(x=>x.placement!=='cover');
      puzzleChapters=p.scenes;
      const choose=el('puzzleIllustration'),old=choose.value;
      choose.replaceChildren();
      for(const asset of puzzleArtwork){
        const option=document.createElement('option');option.value=asset.id;
        const ix=p.scenes.findIndex(c=>c.id===asset.scene_id);
        option.textContent=(ix+1)+'. '+(asset.caption||'Рисунок')+
          ' · '+asset.placement;choose.append(option);
      }
      if([...choose.options].some(x=>x.value===old))choose.value=old;
      syncPuzzleChapters();
      const list=el('puzzleList');list.replaceChildren();
      if(!items.length){list.textContent='Визуальных загадок пока нет.';return;}
      for(const item of items){
        const row=document.createElement('article');row.className='version-issue';
        const from=p.scenes.findIndex(x=>x.id===item.clue_scene_id)+1;
        const to=p.scenes.findIndex(x=>x.id===item.answer_scene_id)+1;
        const heading=document.createElement('strong');heading.textContent=
          'Глава '+from+' → '+to+' · '+item.title;
        const secret=document.createElement('p');secret.textContent=
          'Ответ для редактора (не печатается): '+item.answer_note;
        const remove=document.createElement('button');remove.type='button';
        remove.textContent='Удалить только связь загадки';
        remove.onclick=async()=>{
          if(!confirm('Удалить связку подсказки и разгадки? Сам рисунок сохранится.'))return;
          try{
            await json('/api/projects/'+id+'/visual-puzzles/'+item.id,{method:'DELETE'});
            await loadPuzzles();
          }catch(e){el('puzzleStatus').textContent='Не удалено: '+e.message}
        };
        row.append(heading,secret,remove);list.append(row);
      }
      el('puzzleStatus').textContent=items.length+' визуальных загадок. '+
        'Их ответы видны только в редакторе; изображение ставится в выбранную главу.';
    }catch(e){el('puzzleStatus').textContent='Ошибка загрузки загадок: '+e.message}
  }
  async function savePuzzle(){
    const p=current();if(!p)return;
    const button=el('puzzleSave');button.disabled=true;
    try{
      await json('/api/projects/'+p.id+'/visual-puzzles',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          illustration_id:el('puzzleIllustration').value,
          answer_scene_id:el('puzzleAnswerScene').value,
          title:el('puzzleTitle').value.trim(),
          answer_note:el('puzzleAnswer').value.trim()
        })
      });
      el('puzzleTitle').value='';el('puzzleAnswer').value='';
      await loadPuzzles();
    }catch(e){el('puzzleStatus').textContent='Загадка не добавлена: '+e.message}
    finally{button.disabled=false}
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
    const coverOption=document.createElement('option');coverOption.value=COVER_ID;
    coverOption.textContent='Обложка книги (отдельная страница)';select.append(coverOption);
    for(const chapter of p.scenes) {
      const opt = document.createElement('option');
      opt.value=chapter.id;opt.textContent=chapter.title;select.append(opt);
    }
    if (p.scenes.some(x=>x.id===selected)||selected===COVER_ID) select.value=selected;
    else if(p.scenes.length) select.value=p.scenes[0].id;
    syncPlacement();
    el('illustrationUpload').disabled = imageBusy;
    el('illustrationGenerate').disabled = true;
    el('illustrationRefine').disabled = imageBusy;
    grid.textContent = 'Загружаем обложку и иллюстрации…';
    try {
      const [items,engine,control]=await Promise.all([
        json('/api/projects/'+projectId+'/illustrations'),
        json('/api/illustration-engine').catch(err=>({available:false,reason:err.message})),
        json('/api/illustration-engine/control').catch(()=>null),
      ]);
      if(!current() || current().id!==projectId) return;
      controlStatus=control;
      renderEngineControls(engine,control);
      resumeControlJob(control);
      grid.replaceChildren();
      if(!items.length)grid.textContent=
        'Изображений пока нет. Выбери главу, загрузи PNG/JPEG/WebP или создай рисунок.';
      for(const item of items)grid.append(imageCard(projectId,item,p.scenes));
      el('illustrationGenerate').disabled=!engine.available||imageBusy;
      await loadPuzzles();
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
    let position;try{position=imageParams()}catch(e){alert(e.message);return}
    body.set('placement',position.placement);
    body.set('paragraph_index',String(position.paragraph_index));
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
    let position;try{position=imageParams()}catch(e){alert(e.message);return}
    const body={
      scene_id:el('illustrationScene').value,prompt,
      caption:el('illustrationCaption').value,
      placement:position.placement,
      paragraph_index:position.paragraph_index,
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
        free_requested:'ComfyUI принял запрос на выгрузку модели и освобождение VRAM',
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
                    'illustrationPlacement','illustrationParagraphIndex','illustrationPrompt',
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
  el('illustrationScene').addEventListener('change',syncPlacement);
  el('illustrationPlacement').addEventListener('change',syncPlacement);
  el('puzzleIllustration').addEventListener('change',syncPuzzleChapters);
  el('puzzleSave').addEventListener('click',savePuzzle);
  el('puzzleRefresh').addEventListener('click',loadPuzzles);
  el('illustrationUpload').addEventListener('click',upload);
  el('illustrationRefine').addEventListener('click',refine);
  el('illustrationGenerate').addEventListener('click',generate);
  el('illustrationEnginePrepare').addEventListener('click',prepareEngine);
  el('illustrationEngineStop').addEventListener('click',stopEngine);
  el('illustrationEngineRefresh').addEventListener('click',refresh);
  document.addEventListener('achewiw:tab-shown',event=>{
    if(event.detail?.name==='illustrations') refresh();
  });
  if (!el('illustrations').hidden && current()) refresh();
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
