/* ACHEWIW: real author-owned fragment planning, revisions and continuity QA.
   Text and fields remain authored content. No background auto-acceptance. */
(() => {
  const el = id => document.getElementById(id);
  const asList = text => text.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
  const asText = array => (array || []).join(', ');
  const selected = () => project?.scenes?.find(s => s.id === el('writingScene')?.value);
  const newId = () => crypto.randomUUID().replaceAll('-', '');
  const stored = () => el('saveState').textContent = 'План изменён · нажми «Сохранить план»';
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  function field(label, value, onChange, options = {}) {
    const wrap = make('label', 'block-plan-field');
    const caption = make('span', '', label);
    const node = document.createElement(options.multiline ? 'textarea' : 'input');
    if (options.multiline) node.rows = options.rows || 3;
    else node.type = options.type || 'text';
    if (options.min != null) node.min = String(options.min);
    if (options.max != null) node.max = String(options.max);
    node.value = value ?? '';
    node.addEventListener('input', () => {
      onChange(node.value);
      stored();
    });
    wrap.append(caption, node);
    return wrap;
  }

  function renderBlockPlanner() {
    const pane = el('writingBlockPlanner');
    if (!pane) return;
    pane.hidden = !project || project.work_type !== 'fiction';
    const target = el('writingBlockList');
    target.replaceChildren();
    const scene = selected();
    if (!scene || pane.hidden) return;
    if (!Array.isArray(scene.blocks)) scene.blocks = [];
    const sceneIndex = project.scenes.indexOf(scene);
    const earlier = project.scenes.slice(0, sceneIndex).flatMap(s => s.blocks || [])
      .concat(scene.blocks);
    if (!scene.blocks.length) {
      target.append(make('p', 'block-plan-empty',
        'План пуст. Добавь первый фрагмент: что именно происходит, какую причину нужно показать и сколько примерно слов написать.'));
      return;
    }
    for (const [index, block] of scene.blocks.entries()) {
      const article = make('article', 'planned-block');
      const head = make('div', 'planned-block-head');
      head.append(make('strong', '', 'Фрагмент '+(index+1)));
      const status = make('small', block.accepted_job_id ? '✓ принят автором' : 'ожидает генерации');
      head.append(status);
      const description = field('Что должно произойти в этом фрагменте?',
        block.description, v => block.description = v,
        {multiline:true,rows:3});
      description.querySelector('textarea').maxLength = 4000;
      const volume = field('Цель, слов (80–1000)',
        block.target_words || 350, v => {
          const n = Number(v);
          if (Number.isInteger(n) && n >= 80 && n <= 1000) block.target_words = n;
        }, {type:'number',min:80,max:1000});
      const requires = field('Что уже должно быть введено ранее? (через запятую)',
        asText(block.requires), v => block.requires=asList(v));
      const introduces = field('Что впервые появляется именно здесь?',
        asText(block.introduces), v => block.introduces=asList(v));
      const forbidden = field('Что нельзя внезапно выдумывать?',
        asText(block.forbidden_inventions), v => block.forbidden_inventions=asList(v));
      const style = field('Авторский стиль: ритм, лицо, речь, ограничения',
        block.style||'', v => block.style=v);
      const linkWrap = make('label','block-plan-field');
      linkWrap.append(make('span','','Причина из ранее запланированных блоков'));
      const cause=document.createElement('select');
      const blank=document.createElement('option');
      blank.value='';blank.textContent='Не указана — начало линии / авторская причинность';
      cause.append(blank);
      for(const prev of earlier){
        if(prev.id===block.id)break;
        const option=document.createElement('option');
        option.value=prev.id;
        option.textContent=prev.description.slice(0,90)||prev.id.slice(0,8);
        cause.append(option);
      }
      cause.value=(block.caused_by||[])[0]||'';
      cause.addEventListener('change',()=>{
        block.caused_by=cause.value?[cause.value]:[];stored();
      });
      linkWrap.append(cause);
      const fields = make('div','planned-block-fields');
      fields.append(description,volume,requires,introduces,linkWrap,forbidden,style);
      if(block.accepted_job_id){
        fields.querySelectorAll('input,textarea,select').forEach(node=>node.disabled=true);
      }
      const actions = make('div','planned-block-actions');
      const generate = make('button','primary','✦ Написать этот фрагмент');
      generate.type='button';
      generate.disabled=!!block.accepted_job_id;
      generate.onclick=async()=>{
        if(!scene.blocks.includes(block))return;
        const n=Number(volume.querySelector('input').value);
        if(!block.description.trim()||block.description.trim().length<4){
          toast('Сначала опиши событие фрагмента хотя бы четырьмя символами');return;
        }
        if(!Number.isInteger(n)||n<80||n>1000){
          toast('Укажи объём 80–1000 слов');return;
        }
        block.target_words=n;
        await ai('block',{block_id:block.id});
      };
      actions.append(generate);
      if (!block.accepted_job_id) {
        const remove=make('button','','Убрать из плана');
        remove.type='button';
        remove.onclick=()=>{
          if(!confirm('Удалить план фрагмента? Уже сохранённые тексты книги не изменятся.'))return;
          scene.blocks.splice(index,1);
          stored();renderBlockPlanner();
        };
        actions.append(remove);
      }
      article.append(head,fields,actions);
      target.append(article);
    }
  }

  async function saveBlockPlan(){
    if(!project)return;
    collectScenes();collectText();
    try{
      await save();
      toast('План фрагментов и причинные связи сохранены');
    }catch(err){toast('Не удалось сохранить план: '+err.message)}
  }

  async function renderContinuity(){
    const node=el('continuityFindings');
    if(!node)return;
    if(!project||project.work_type!=='fiction'){
      node.hidden=true;
      return;
    }
    node.hidden=false;
    const scope=el('continuityScene');
    const selectedScope=scope.value;
    scope.replaceChildren();
    const all=document.createElement('option');all.value='';
    all.textContent='Вся история: обзор карточек и реестра';
    scope.append(all);
    for(const scene of project.scenes){
      const option=document.createElement('option');
      option.value=scene.id;option.textContent=scene.title;
      scope.append(option);
    }
    if(project.scenes.some(s=>s.id===selectedScope))scope.value=selectedScope;
    node.textContent='Проверяем явные причины и появление предметов…';
    try{
      const projectId=project.id;
      const response=await api('/projects/'+projectId+'/continuity',{cache:'no-store'});
      if(project?.id!==projectId)return;
      node.replaceChildren();
      const title=make('strong','', 'Проверяемые несостыковки по карточкам: '+response.causality.issues.length);
      node.append(title);
      const warning=make('p','','Это проверка явно заданных причин и предметов, а не доказательство сюжетной ошибки. Для анализа прозы целиком используй кнопку «Проверить причинность».');
      node.append(warning);
      for(const issue of response.causality.issues){
        const line=make('div','causal-warning');
        line.append(make('strong','',issue.scene_title+' · '+issue.subject));
        line.append(make('span','',issue.detail));
        node.append(line);
      }
      const styled=response.style.filter(s=>s.report.cliches.length||s.report.repeated_paragraphs.length);
      if(styled.length){
        node.append(make('h4','','Стилистические сигналы (не детектор ИИ)'));
        for(const s of styled){
          node.append(make('p','',s.title+': '+
            s.report.cliches.map(x=>x.phrase+' ×'+x.occurrences).join(', ')+
            (s.report.repeated_paragraphs.length?' · повторяются абзацы':'')
          ));
        }
      }
      if(!response.causality.issues.length&&!styled.length){
        node.append(make('p','','Явных проблем по заполненным карточкам не найдено. Это не заменяет литературную проверку содержимого.'));
      }
    }catch(err){node.textContent='Проверка недоступна: '+err.message}
  }

  async function revisions(){
    const scene=selected();
    if(!project||!scene)return;
    const host=el('sceneRevisions');
    host.hidden=false;
    host.textContent='Загружаем историю решений автора…';
    try{
      const entries=await api('/projects/'+project.id+'/scenes/'+scene.id+'/revisions');
      host.replaceChildren();
      if(!entries.length){host.textContent='Пока нет принятых предложений';return}
      for(const entry of entries){
        const row=make('div','revision-row');
        row.append(make('span','',new Date(entry.created_at).toLocaleString()+' · '+entry.job_id.slice(0,10)));
        const button=make('button','','Вернуть текст до этой правки');
        button.type='button';
        button.onclick=async()=>{
          if(!confirm('Восстановить предыдущее состояние сцены? Последующие изменения приведут к отказу безопасного отката.'))return;
          try{
            collectScenes();collectText();await save();
            await api('/projects/'+project.id+'/scenes/'+scene.id+'/revisions/'+entry.id+'/restore',
              {method:'POST',body:JSON.stringify({confirm:true})});
            const id=project.id;
            project=await api('/projects/'+id);
            render();
            el('writingScene').value=scene.id;switchWriting();
            await revisions();
            toast('Сцена восстановлена из сохранённой ревизии');
          }catch(err){toast('Восстановление остановлено: '+err.message)}
        };
        row.append(button);host.append(row);
      }
    }catch(err){host.textContent='Ошибка истории: '+err.message}
  }

  const oldSwitchWriting=switchWriting;
  switchWriting=function(){oldSwitchWriting();renderBlockPlanner()};
  el('addWritingBlock').onclick=()=>{
    const scene=selected();
    if(!scene)return;
    if(!Array.isArray(scene.blocks))scene.blocks=[];
    if(scene.blocks.length>=80){toast('В сцене уже 80 фрагментов');return}
    scene.blocks.push({
      id:newId(),description:'',target_words:350,requires:[],introduces:[],
      caused_by:[],forbidden_inventions:[],style:'',accepted_job_id:null,accepted_at:null
    });
    renderBlockPlanner();stored();
  };
  el('saveBlockPlans').onclick=saveBlockPlan;
  el('polishScene').onclick=()=>ai('polish',{instructions:el('polishInstructions').value.trim()});
  el('loadSceneRevisions').onclick=revisions;
  el('runContinuity').onclick=()=>ai('continuity',{scene_id:el('continuityScene').value||null,instructions:el('continuityInstructions').value.trim()});
  document.querySelector('[data-tab="audit"]')?.addEventListener('click',renderContinuity);
})();
