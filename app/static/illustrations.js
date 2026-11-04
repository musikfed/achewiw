/* ACHEWIW illustration studio: real ComfyUI; optional author-reviewed Ollama
   image prompt polish. Never silently allow concurrent ACHEWIW GPU jobs. */
(() => {
  const el = id => document.getElementById(id);
  const current = () => typeof project === 'object' ? project : null;
  let imageBusy = false;
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
    if(!p.scenes.length) {
      grid.textContent = 'Сначала добавь главу в разделе «02 · План».';
      return;
    }
    grid.textContent = 'Загружаем иллюстрации…';
    try {
      const [items,engine]=await Promise.all([
        json('/api/projects/'+projectId+'/illustrations'),
        json('/api/illustration-engine'),
      ]);
      if(!current() || current().id!==projectId) return;
      el('illustrationEngineStatus').textContent=engine.available ?
        '● ComfyUI готов · автоматически выбран checkpoint: '+engine.model :
        'Генерация пока недоступна: '+(engine.reason||'нет графической модели');
      el('illustrationGenerate').disabled=!engine.available||imageBusy;
      grid.replaceChildren();
      if(!items.length)grid.textContent=
        'Изображений пока нет. Выбери главу, загрузи PNG/JPEG/WebP или создай рисунок.';
      for(const item of items)grid.append(imageCard(projectId,item,p.scenes));
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
      setStatus('3/3 · Рисунок сохранён. ComfyUI VRAM: '+result.comfy_vram+
        '. Ollama без запроса не трогали.');
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
  document.querySelector('[data-tab="illustrations"]').addEventListener('click',refresh);
  el('openIllustrations').addEventListener('click',()=>{
    if(!current())return;
    showTab('illustrations');
    refresh();
    el('illustrations').scrollIntoView({behavior:'smooth',block:'start'});
  });
  el('openTypography').addEventListener('click',()=>{
    if(!current())return;
    showTab('book');
    el('book').scrollIntoView({behavior:'smooth',block:'start'});
  });
})();
