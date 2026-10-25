(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PulseModel = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const roles = {financial_director:'Финансовый директор', ceo:'Генеральный директор', treasurer:'Казначей'};
  const priorities = {high:3, medium:2, low:1};
  const array = value => Array.isArray(value) ? value : [];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const safeUrl = value => {try {const url=new URL(value); return ['https:','http:'].includes(url.protocol) ? url.href : null;} catch {return null;}};
  const validDate = value => typeof value==='string' && /^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value.slice(0,10)+'T12:00:00Z').toISOString().slice(0,10)===value.slice(0,10);
  function validateDataset(value) {
    if (!value || value.schema_version !== '3.0' || typeof value.is_demo !== 'boolean' || !Array.isArray(value.companies) || !Array.isArray(value.events)) throw new Error('Структура данных не соответствует версии 3.0.');
    if(value.as_of !== undefined && !validDate(value.as_of)) throw new Error('Некорректная дата среза данных.');
    const unique = (items, kind) => {const ids=new Set(); for(const item of items){if(!item || typeof item.id!=='string' || !item.id || ids.has(item.id)) throw new Error(`Некорректный идентификатор: ${kind}.`);ids.add(item.id);} return ids;};
    const companyIds = unique(value.companies,'компании');
    unique(value.events,'события');
    const companies=value.companies.map(c=>{
      if(typeof c.display_name!=='string' || !c.display_name) throw new Error('Нет названия компании.');
      return {...c,features:array(c.features).filter(f=>f && typeof f.field==='string'),missing_data:array(c.missing_data)};
    });
    const events=value.events.map(event=>{
      if(typeof event.title!=='string' || !event.title || !Array.isArray(event.company_ids) || event.company_ids.some(id=>!companyIds.has(id)) || !Object.hasOwn(priorities,event.priority) || !validDate(event.date)) throw new Error('В событии отсутствуют обязательные данные.');
      const sources=array(event.sources).filter(s=>s && typeof s.id==='string');
      unique(sources,'источники');
      const sourceIds = new Set(sources.map(s=>s.id));
      const facts=array(event.facts).filter(f=>f && typeof f.text==='string').map(f=>({...f,has_source:sourceIds.has(f.source_id)}));
      const ai=event.ai_interpretation || {};
      const bank=event.bank_view || {status:'not_loaded',summary:null,message:'Материалы банка не загружены. Позиция банка не представлена.'};
      // A bank attribution requires explicit available status and every linked document.
      const canShowBank = bank.status==='available' && typeof bank.summary==='string' && bank.summary.trim() && array(bank.source_ids).length && bank.source_ids.every(id=>sources.some(s=>s.id===id && s.type==='bank_analysis' && s.is_demo===false && safeUrl(s.url)));
      return {...event,sources,facts,relevance:event.relevance && typeof event.relevance==='object' ? event.relevance : {},
        bank_view: canShowBank ? bank : {...bank,status:'not_loaded',summary:null},
        ai_interpretation:{...ai,assumptions:array(ai.assumptions).filter(s=>typeof s==='string'),risks:array(ai.risks).filter(s=>s&&typeof s.text==='string'),opportunities:array(ai.opportunities).filter(s=>s&&typeof s.text==='string'),reasoning_steps:array(ai.reasoning_steps).filter(s=>typeof s==='string')},
        actions:array(event.actions).filter(a=>a && typeof a.id==='string' && typeof a.title==='string'),missing_data:array(event.missing_data),
        suggested_questions:array(event.suggested_questions).filter(q=>q && typeof q.question==='string' && typeof q.answer==='string'),
        verification:{status:'needs_more_data',...event.verification,conflicts:array(event.verification?.conflicts)}};
    });
    return {...value,companies,events};
  }
  function getEvents(data, {companyId,role='financial_director',query='',category='all',priority='all',period='all',bookmarked,sort='relevance'}={}) {
    const asOf=new Date((data.as_of || '2026-10-22').slice(0,10)+'T12:00:00Z').getTime();
    const search=query.trim().toLocaleLowerCase('ru');
    return data.events.filter(event=>event.company_ids.includes(companyId))
      .filter(event=>category==='all' || event.category===category)
      .filter(event=>priority==='all' || event.priority===priority)
      .filter(event=>!bookmarked || bookmarked.includes(event.id))
      .filter(event=>period==='all' || ((asOf-new Date(event.date.slice(0,10)+'T12:00:00Z').getTime())>=0 && (asOf-new Date(event.date.slice(0,10)+'T12:00:00Z').getTime())<=Number(period)*86400000))
      .filter(event=>!search || [event.title,event.summary,event.category_label,event.relevance[companyId]?.reason,event.relevance[companyId]?.summary].join(' ').toLocaleLowerCase('ru').includes(search))
      .sort((a,b)=>sort==='date' ? b.date.localeCompare(a.date) : score(b,companyId,role)-score(a,companyId,role) || b.date.localeCompare(a.date));
  }
  function score(event, companyId,role) {const r=event.relevance[companyId] || {}; const value=Number(r.role_scores?.[role] ?? r.score ?? 0);return Number.isFinite(value)?value:0;}
  function impact(event,companyId,role) {const r=event.relevance[companyId] || {};return r.role_summary?.[role] || r.summary || event.ai_interpretation.summary || 'Интерпретация ещё не подготовлена.';}
  const stateKey=(companyId,event)=>`${companyId}:${event.id}:${event.date}`;
  function featureText(feature) {
    if (typeof feature.value==='boolean') return feature.value ? 'Есть' : 'Нет';
    if(feature.value==null || feature.value==='') return 'Нет данных';
    if(feature.unit==='RUB' && typeof feature.value==='number') return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(feature.value/1e9)+' млрд ₽';
    if(feature.unit==='%') return String(feature.value)+'%';
    return String(feature.value);
  }
  return {roles,priorities,array,escape,safeUrl,validateDataset,getEvents,score,impact,stateKey,featureText};
});
