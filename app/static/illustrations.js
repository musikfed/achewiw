/* ACHEWIW illustration studio — real PNG storage, optional local ComfyUI.
   No fabricated images and no automatic unload of shared Ollama models. */
(() => {
  const el = id => document.getElementById(id);
  const current = () => typeof project === 'object' ? project : null;

  async function json(url, options={}) {
    const res = await fetch(url, {cache:'no-store', ...options});
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : 'HTTP '+res.status);
    }
    return res.status===204 ? null : await res.json();
  }

  function imageCard(projectId, row, chapters) {
    const card = document.createElement('article');
    card.className='illustration-card';
    const figure=document.createElement('figure');
    const img=document.createElement('img');
    img.src='/api/projects/'+encodeURIComponent(projectId)+'/illustrations/'+encodeURIComponent(row.id)+'.png';
    img.alt=row.caption||'Иллюстрация';
    img.loading='lazy';
    const cap=document.createElement('figcaption');
    cap.textContent=row.caption||'Без подписи';
    figure.append(img,cap);
    const meta=document.createElement('div');
    meta.className='illustration-meta';
    const title=document.createElement('p');
    title.textContent=(chapters.find(x=>x.id===row.scene_id)?.title||'Глава')+
      ' · '+(row.placement==='end'?'после текста':'после заголовка');
    const remove=document.createElement('button');
    remove.type='button';
    remove.className='danger-action';
    remove.textContent='Удалить изображение';
    remove.onclick=async()=>{
      if(!confirm('Удалить именно этот рисунок из текущей книги?'))return;
      try{
        await json('/api/projects/'+projectId+'/illustrations/'+row.id,{method:'DELETE'});
        await refresh();
      }catch(e){el('illustrationEngineStatus').textContent='Удаление не выполнено: '+e.message}
    };
    meta.append(title,remove);
    card.append(figure,meta);
    return card;
  }

  async function refresh() {
    const p=current();
    if(!p)return;
    const projectId=p.id;
    const grid=el('illustrationGrid');
    const select=el('illustrationScene');
    select.replaceChildren();
    for(const chapter of p.scenes) {
      const opt=document.createElement('option');
      opt.value=chapter.id;
      opt.textContent=chapter.title;
      select.append(opt);
    }
    el('illustrationUpload').disabled=!p.scenes.length;
    el('illustrationGenerate').disabled=true;
    if(!p.scenes.length){
      grid.textContent='Сначала добавь главу или сцену в разделе «02 · План».';
      return;
    }
    grid.textContent='Загружаем иллюстрации…';
    try{
      const [items,engine]=await Promise.all([
        json('/api/projects/'+projectId+'/illustrations'),
        json('/api/illustration-engine')
      ]);
      if(!current()||current().id!==projectId)return;
      el('illustrationEngineStatus').textContent=engine.available ?
        '● ComfyUI готов · автоматический выбор checkpoint: '+engine.model :
        'Генерация недоступна: '+(engine.reason||'нет графической модели');
      el('illustrationGenerate').disabled=!engine.available;
      grid.replaceChildren();
      if(!items.length)grid.textContent='Изображений пока нет. Выбери главу и загрузи PNG, JPEG или WebP.';
      for(const item of items)grid.append(imageCard(projectId,item,p.scenes));
    }catch(err){
      grid.textContent='Ошибка загрузки: '+err.message;
      el('illustrationEngineStatus').textContent='Не удалось проверить графический движок';
    }
  }

  async function upload() {
    const p=current(), file=el('illustrationFile').files[0];
    if(!p)return;
    if(!file){alert('Выбери PNG, JPEG или WebP');return}
    const body=new FormData();
    body.set('file',file,file.name);
    body.set('scene_id',el('illustrationScene').value);
    body.set('caption',el('illustrationCaption').value);
    body.set('placement',el('illustrationPlacement').value);
    el('illustrationUpload').disabled=true;
    try{
      await json('/api/projects/'+p.id+'/illustrations',{method:'POST',body});
      el('illustrationFile').value='';
      await refresh();
    }catch(err){alert('Не удалось добавить изображение: '+err.message)}
    finally{el('illustrationUpload').disabled=false}
  }

  async function generate() {
    const p=current();
    if(!p)return;
    const prompt=el('illustrationPrompt').value.trim();
    if(!prompt){alert('Сначала опиши композицию картинки');return}
    const button=el('illustrationGenerate');
    button.disabled=true;
    const body={
      scene_id:el('illustrationScene').value,
      prompt,
      caption:el('illustrationCaption').value,
      placement:el('illustrationPlacement').value
    };
    const url='/api/projects/'+p.id+'/illustrations/generate';
    const post=payload=>fetch(url,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    el('illustrationEngineStatus').textContent='ComfyUI создаёт изображение, общая Ollama не выгружается…';
    try{
      let response=await post(body);
      if(response.status===409){
        const data=await response.json();
        if(!confirm(data.detail+'\nРазрешить параллельное использование видеопамяти?'))return;
        response=await post({...body,confirm_shared_gpu:true});
      }
      if(!response.ok){
        const data=await response.json().catch(()=>({}));
        throw Error(data.detail||'HTTP '+response.status);
      }
      const result=await response.json();
      el('illustrationPrompt').value='';
      alert('Настоящее изображение сохранено в книге. Модель: '+result.checkpoint);
    }catch(err){alert('Генерация не удалась: '+err.message)}
    finally{await refresh()}
  }

  el('illustrationUpload').addEventListener('click',upload);
  el('illustrationGenerate').addEventListener('click',generate);
  document.querySelector('[data-tab="illustrations"]').addEventListener('click',()=>refresh());
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
