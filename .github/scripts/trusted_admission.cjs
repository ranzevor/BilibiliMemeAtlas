'use strict';
const crypto=require('node:crypto');
const fs=require('node:fs');
const policy=require('./governance_gate.cjs');

const REPOSITORY='ranzevor/BilibiliMemeAtlas';
const REPOSITORY_ID=1438097780;
const CHECK='Trusted metadata gate';
const ACTIONS_APP=15368;
const QUALITY=['Project gate','Lifecycle gate','Release bytes gate'];
const TARGETS=[
  'refs/heads/main',
  'refs/heads/release/v0.1.5-queue-item-*',
  'refs/heads/fix/v0.1.5-duplicate-source-reference',
  'refs/heads/fix/v0.1.5-duplicate-source-reference-rebuild',
  'refs/heads/feat/governance-pr-metadata-gate'
];
const PREFIX='/repos/'+REPOSITORY;
const SHA=/^[a-f0-9]{40}$/u;

function positive(value,label) {
  const n=Number(value);
  if(!Number.isSafeInteger(n)||n<=0) throw new Error(label+' 必须是正整数');
  return n;
}
function supported(ref) {
  return typeof ref==='string'&&TARGETS.some(pattern=>{
    const name=pattern.slice('refs/heads/'.length);
    const prefix=name.slice(0,-1);
    return name.endsWith('*')?ref.startsWith(prefix)&&!ref.slice(prefix.length).includes('/'):ref===name;
  });
}
function identity(pr,number) {
  if(pr.number!==number||pr.base?.repo?.id!==REPOSITORY_ID||
     pr.base.repo.full_name!==REPOSITORY||!SHA.test(pr.head?.sha)||
     !SHA.test(pr.base?.sha)||!SHA.test(pr.merge_commit_sha||''))
    throw new Error('PR 身份、提交或合并候选无效');
  if(!supported(pr.base.ref)) throw new Error('PR 目标分支不在受支持范围');
}
function fingerprint(pr) {
  return JSON.stringify([
    pr.number,pr.state,pr.merged,pr.draft,pr.title,pr.body,pr.updated_at,
    pr.head?.ref,pr.head?.sha,pr.head?.repo?.id,pr.base?.ref,pr.base?.sha,
    pr.base?.repo?.id,pr.merge_commit_sha,pr.commits,pr.changed_files
  ]);
}
function squashMessage(pr) {
  return {commit_title:pr.title,commit_message:pr.body,sha:pr.head.sha,merge_method:'squash'};
}
function fullMessage(message) {
  return message.commit_title+'\n\n'+message.commit_message;
}
function rulesetPlan(appId) {
  appId=positive(appId,'独立 App ID');
  if(appId===ACTIONS_APP) throw new Error('独立检查不能使用 GitHub Actions 集成身份');
  const conditions={ref_name:{include:[...TARGETS],exclude:[]}};
  return [
    {
      name:'受支持目标分支保护',target:'branch',enforcement:'active',
      bypass_actors:[],conditions,
      rules:[
        {type:'deletion'},{type:'non_fast_forward'},
        {type:'pull_request',parameters:{
          required_approving_review_count:0,dismiss_stale_reviews_on_push:true,
          require_code_owner_review:false,require_last_push_approval:false,
          required_review_thread_resolution:true,allowed_merge_methods:['squash']
        }},
        {type:'required_status_checks',parameters:{
          strict_required_status_checks_policy:true,do_not_enforce_on_create:true,
          required_status_checks:[
            ...QUALITY.map(context=>({context,integration_id:ACTIONS_APP})),
            {context:CHECK,integration_id:appId}
          ]
        }}
      ]
    },
    {
      name:'受控 squash 合并',target:'branch',enforcement:'active',conditions,
      bypass_actors:[{actor_id:appId,actor_type:'Integration',bypass_mode:'always'}],
      rules:[{type:'update',parameters:{update_allows_fetch_and_merge:false}}]
    }
  ];
}

class GitHub {
  constructor(token,{fetchImpl=fetch}={}) {
    this.token=token;
    this.fetchImpl=fetchImpl;
  }
  async request(method,path,body) {
    if(!path.startsWith('/')||path.startsWith('//')) throw new Error('GitHub API 路径无效');
    const token=await this.token();
    const response=await this.fetchImpl('https://api.github.com'+path,{
      method,redirect:'error',signal:AbortSignal.timeout(20000),
      headers:{
        Accept:'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28',
        Authorization:'Bearer '+token,
        'Content-Type':'application/json'
      },
      body:body===undefined?undefined:JSON.stringify(body)
    });
    if(!response.ok) throw new Error('GitHub API '+method+' '+path+' 返回 '+response.status);
    const text=await response.text();
    if(text.length>16*1024*1024) throw new Error('GitHub API 响应超过长度上限');
    return text?JSON.parse(text):null;
  }
  async list(path) {
    const all=[];
    for(let page=1;page<=11;page++) {
      const rows=await this.request('GET',path+(path.includes('?')?'&':'?')+'per_page=100&page='+page);
      if(!Array.isArray(rows)) throw new Error('GitHub API 列表响应无效');
      all.push(...rows);
      if(rows.length<100) return all;
    }
    throw new Error('GitHub API 列表超过可核验范围');
  }
}

async function installationClient(env=process.env,fetchImpl=fetch) {
  const appId=positive(env.GOVERNANCE_APP_ID,'App ID');
  const installationId=positive(env.GOVERNANCE_INSTALLATION_ID,'Installation ID');
  rulesetPlan(appId);
  if(!env.GOVERNANCE_PRIVATE_KEY_FILE) throw new Error('缺少 App 私钥文件路径');
  const key=crypto.createPrivateKey(fs.readFileSync(env.GOVERNANCE_PRIVATE_KEY_FILE));
  if(key.asymmetricKeyType!=='rsa') throw new Error('App 私钥必须是 RSA 密钥');
  const jwt=()=>{
    const now=Math.floor(Date.now()/1000);
    const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url');
    const unsigned=encode({alg:'RS256',typ:'JWT'})+'.'+encode({iat:now-60,exp:now+540,iss:appId});
    return unsigned+'.'+crypto.sign('RSA-SHA256',Buffer.from(unsigned),key).toString('base64url');
  };
  const app=new GitHub(async()=>jwt(),{fetchImpl});
  const application=await app.request('GET','/app');
  if(application.id!==appId) throw new Error('App 身份与配置不一致');
  const installation=await app.request('GET','/app/installations/'+installationId);
  if(installation.app_id!==appId||installation.account?.login!=='ranzevor')
    throw new Error('App 安装目标与仓库所有者不一致');
  let credential=null;
  const client=new GitHub(async()=>{
    if(!credential||Date.parse(credential.expires_at)-Date.now()<60000) {
      credential=await app.request('POST','/app/installations/'+installationId+'/access_tokens',{
        repository_ids:[REPOSITORY_ID],
        permissions:{contents:'write',checks:'write',pull_requests:'read'}
      });
      if(typeof credential.token!=='string'||!Number.isFinite(Date.parse(credential.expires_at)))
        throw new Error('安装令牌响应无效');
    }
    return credential.token;
  },{fetchImpl});
  client.appId=appId;
  if(env.GOVERNANCE_RULES_FILE) {
    const record=JSON.parse(fs.readFileSync(env.GOVERNANCE_RULES_FILE,'utf8'));
    if(record.repository!==REPOSITORY||record.repository_id!==REPOSITORY_ID||
       record.app_id!==appId||!Array.isArray(record.rulesets)||
       record.rulesets.some(rule=>!Number.isSafeInteger(rule.id)||!Array.isArray(rule.bypass_actors)||
         !Number.isFinite(Date.parse(rule.updated_at))||rule.source!==REPOSITORY))
      throw new Error('管理员规则版本记录的身份或内容无效');
    client.ruleVersions=new Map(record.rulesets.map(rule=>[rule.id,rule]));
    if(client.ruleVersions.size!==record.rulesets.length) throw new Error('管理员规则版本记录含重复对象');
  }
  return client;
}

async function evaluate(client,number) {
  number=positive(number,'PR 编号');
  const repository=await client.request('GET',PREFIX);
  if(repository.id!==REPOSITORY_ID||repository.full_name!==REPOSITORY)
    throw new Error('仓库身份与配置不一致');
  const endpoint=PREFIX+'/pulls/'+number;
  const pr=await client.request('GET',endpoint);
  identity(pr,number);
  const errors=[];
  if(pr.state!=='open'||pr.merged||pr.draft) errors.push('PR 必须处于开放且可审查状态');
  if(pr.mergeable!==true) errors.push('PR 合并候选尚不可用或存在冲突');
  if(!Number.isSafeInteger(pr.commits)||pr.commits<1||pr.commits>250||
     !Number.isSafeInteger(pr.changed_files)||pr.changed_files<1||pr.changed_files>500)
    throw new Error('PR 提交或文件数量超出可核验范围');
  const [commits,files]=await Promise.all([
    client.list(endpoint+'/commits'),client.list(endpoint+'/files')
  ]);
  if(commits.length!==pr.commits||files.length!==pr.changed_files)
    throw new Error('PR 提交或文件列表不完整');
  if(commits.some(c=>!SHA.test(c.sha)||typeof c.commit?.message!=='string'))
    throw new Error('PR 提交列表包含无效对象');
  errors.push(...policy.prErrors(pr,commits,files));
  const message=squashMessage(pr);
  errors.push(...policy.commitErrors(fullMessage(message)).map(e=>'最终 squash：'+e));
  const latest=await client.request('GET',endpoint);
  identity(latest,number);
  if(fingerprint(latest)!==fingerprint(pr)) errors.push('PR 在校验期间变化，须重新检查');
  return {pr,message,errors};
}

async function openGroups(client) {
  const pulls=await client.list(PREFIX+'/pulls?state=open');
  const groups=new Map();
  for(const pr of pulls) {
    if(!supported(pr.base?.ref)) continue;
    if(!SHA.test(pr.head?.sha)||pr.base?.repo?.id!==REPOSITORY_ID)
      throw new Error('开放 PR 列表包含无效身份');
    if(!groups.has(pr.head.sha)) groups.set(pr.head.sha,[]);
    groups.get(pr.head.sha).push(pr);
  }
  for(const rows of groups.values()) rows.sort((a,b)=>a.number-b.number);
  return groups;
}
function groupFingerprint(rows) {
  return JSON.stringify(rows.map(pr=>fingerprint(pr)));
}
async function publishGroup(client,head,rows) {
  if(!SHA.test(head)||!rows.length||rows.some(pr=>pr.head?.sha!==head))
    throw new Error('检查对象无效');
  const check=await client.request('POST',PREFIX+'/check-runs',{
    name:CHECK,head_sha:head,status:'in_progress'
  });
  if(check.app?.id!==client.appId||!Number.isSafeInteger(check.id))
    throw new Error('检查来源不是配置的独立 App');
  const errors=[];
  let retry=false;
  const reports=[];
  try {
    for(const row of rows) {
      const report=await evaluate(client,row.number);
      reports.push(report);
      if(report.pr.head.sha!==head) errors.push('PR #'+row.number+' 的 HEAD 已变化');
      errors.push(...report.errors.map(e=>'PR #'+row.number+'：'+e));
    }
    const current=(await openGroups(client)).get(head)||[];
    if(groupFingerprint(current)!==groupFingerprint(rows)) {
      errors.push('同一提交的开放 PR 集合或元数据已变化，须重新检查');
      retry=true;
    }
  } catch(error) {
    errors.push(error.message);
    retry=true;
  }
  await client.request('PATCH',PREFIX+'/check-runs/'+check.id,{
    status:'completed',conclusion:errors.length?'failure':'success',
    completed_at:new Date().toISOString(),
    output:{
      title:errors.length?'提交与 PR 校验失败':'提交与 PR 校验通过',
      summary:errors.length?errors.join('\n').slice(0,60000):
        '已核验当前提交对应的全部受支持开放 PR、完整提交和差异，以及待写入的 squash 消息。'
    }
  });
  return {reports,errors,retry};
}

function validBaseline(ruleset,appId) {
  if(ruleset.enforcement!=='active'||!Array.isArray(ruleset.bypass_actors)||
     ruleset.bypass_actors.length||!Array.isArray(ruleset.rules)) return false;
  const rules=ruleset.rules;
  const pr=rules.find(r=>r.type==='pull_request')?.parameters;
  const checks=rules.find(r=>r.type==='required_status_checks')?.parameters;
  return rules.some(r=>r.type==='deletion')&&rules.some(r=>r.type==='non_fast_forward')&&
    pr?.required_review_thread_resolution===true&&
    JSON.stringify(pr.allowed_merge_methods)===JSON.stringify(['squash'])&&
    checks?.strict_required_status_checks_policy===true&&
    [...QUALITY.map(context=>({context,integration_id:ACTIONS_APP})),{context:CHECK,integration_id:appId}]
      .every(expected=>checks.required_status_checks?.some(c=>
        c.context===expected.context&&c.integration_id===expected.integration_id));
}
function validUpdateGate(ruleset,appId) {
  return ruleset.enforcement==='active'&&ruleset.rules?.some(r=>
    r.type==='update'&&(r.parameters===undefined||r.parameters?.update_allows_fetch_and_merge===false))&&
    ruleset.rules.length===1&&ruleset.bypass_actors?.length===1&&
    ruleset.bypass_actors[0].actor_id===appId&&
    ruleset.bypass_actors[0].actor_type==='Integration'&&
    ruleset.bypass_actors[0].bypass_mode==='always';
}
function canonical(value) {
  if(Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if(value&&typeof value==='object') return '{'+Object.keys(value).filter(key=>value[key]!==undefined)
    .sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
  return JSON.stringify(value);
}
function rulesetMaterial(rule) {
  return {id:rule.id,target:rule.target,source_type:rule.source_type,source:rule.source,
    enforcement:rule.enforcement,conditions:rule.conditions,rules:rule.rules};
}
function completeRuleset(client,live) {
  if(Array.isArray(live.bypass_actors)) return live;
  if(Object.hasOwn(live,'bypass_actors')) throw new Error('目标分支缺少有效的旁路名单响应');
  const full=client.ruleVersions?.get(live.id);
  const expectedBypass=full&&validBaseline(full,client.appId)?'never':
    full&&validUpdateGate(full,client.appId)?'always':null;
  if(!full||!expectedBypass||!Array.isArray(full.bypass_actors)||
     !Number.isFinite(Date.parse(live.updated_at))||
     Date.parse(live.updated_at)!==Date.parse(full.updated_at)||
     canonical(rulesetMaterial(live))!==canonical(rulesetMaterial(full))||
     live.current_user_can_bypass!==expectedBypass)
    throw new Error('目标分支缺少可核验的完整规则版本，须由有权账号重新读取当前规则');
  return {...live,bypass_actors:full.bypass_actors};
}
async function auditRules(client,appId) {
  rulesetPlan(appId);
  const repository=await client.request('GET',PREFIX);
  if(repository.id!==REPOSITORY_ID||repository.full_name!==REPOSITORY)
    throw new Error('管理员规则读取的仓库身份不符');
  const list=await client.list(PREFIX+'/rulesets?includes_parents=true');
  const candidates=list.filter(rule=>rule.enforcement==='active'&&rule.target==='branch');
  const full=await Promise.all(candidates.map(rule=>client.request('GET',PREFIX+'/rulesets/'+rule.id)));
  const sameScope=rule=>rule.source===REPOSITORY&&rule.target==='branch'&&
    Array.isArray(rule.conditions?.ref_name?.include)&&
    canonical([...rule.conditions.ref_name.include].sort())===canonical([...TARGETS].sort())&&
    Array.isArray(rule.conditions.ref_name.exclude)&&rule.conditions.ref_name.exclude.length===0;
  const baseline=full.filter(rule=>sameScope(rule)&&validBaseline(rule,appId));
  const update=full.filter(rule=>sameScope(rule)&&validUpdateGate(rule,appId));
  if(baseline.length!==1||update.length!==1||
     [...baseline,...update].some(rule=>!Number.isSafeInteger(rule.id)||!Number.isFinite(Date.parse(rule.updated_at))))
    throw new Error('缺少完整且唯一的管理员规则视图，不能生成版本记录');
  return {schema:1,repository:REPOSITORY,repository_id:REPOSITORY_ID,app_id:appId,
    rulesets:[baseline[0],update[0]]};
}
async function requireProtection(client,ref) {
  const effective=await client.request('GET',PREFIX+'/rules/branches/'+encodeURIComponent(ref));
  if(!Array.isArray(effective)) throw new Error('生效分支规则响应无效');
  const ids=[...new Set(effective.map(r=>r.ruleset_id))];
  if(ids.some(id=>!Number.isSafeInteger(id))) throw new Error('生效规则缺少规则集身份');
  const live=await Promise.all(ids.map(id=>client.request('GET',PREFIX+'/rulesets/'+id)));
  const sets=live.filter(rule=>Array.isArray(rule.bypass_actors)||client.ruleVersions?.has(rule.id))
    .map(rule=>completeRuleset(client,rule));
  if(!sets.some(s=>validBaseline(s,client.appId))||!sets.some(s=>validUpdateGate(s,client.appId)))
    throw new Error('目标分支缺少不可绕过的质量规则或独立 App 更新限制');
}
async function verifyMerge(client,report,expectedSha) {
  const pr=await client.request('GET',PREFIX+'/pulls/'+report.pr.number);
  if(!pr.merged||!SHA.test(pr.merge_commit_sha||'')||
     (expectedSha&&pr.merge_commit_sha!==expectedSha))
    throw new Error('合并结果尚未确认；须查询当前 PR，禁止重复请求合并');
  const commit=await client.request('GET',PREFIX+'/git/commits/'+pr.merge_commit_sha);
  const message=typeof commit.message==='string'?commit.message:'';
  if(commit.sha!==pr.merge_commit_sha||commit.parents?.length!==1||
     commit.parents[0].sha!==report.pr.base.sha||
     message.replace(/\n$/u,'')!==fullMessage(report.message).replace(/\n$/u,'')||
     policy.commitErrors(message).length)
    throw new Error('实际 squash 提交与已验证候选不一致，须核查目标分支');
  return {sha:commit.sha,merged:true};
}
async function merge(client,number) {
  number=positive(number,'PR 编号');
  const groups=await openGroups(client);
  const entry=[...groups.entries()].find(([,rows])=>rows.some(pr=>pr.number===number));
  if(!entry) throw new Error('未找到受支持的开放 PR');
  const outcome=await publishGroup(client,...entry);
  if(outcome.errors.length) throw new Error(outcome.errors.join('\n'));
  const report=outcome.reports.find(r=>r.pr.number===number);
  await requireProtection(client,report.pr.base.ref);
  const final=await evaluate(client,number);
  if(final.errors.length||fingerprint(final.pr)!==fingerprint(report.pr))
    throw new Error('合并前 PR 状态已变化或校验失败');
  let response;
  try {
    response=await client.request('PUT',PREFIX+'/pulls/'+number+'/merge',final.message);
  } catch(error) {
    try { return await verifyMerge(client,final); }
    catch { throw new Error(error.message+'；合并结果须查询确认，禁止盲目重试'); }
  }
  if(response.merged!==true||!SHA.test(response.sha||''))
    throw new Error('GitHub 未确认合并成功');
  return verifyMerge(client,final,response.sha);
}

async function watch(client,{sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}) {
  const previous=new Map();
  for(;;) {
    try {
      const groups=await openGroups(client);
      for(const [head,rows] of groups) {
        const signature=groupFingerprint(rows);
        if(previous.get(head)===signature) continue;
        const result=await publishGroup(client,head,rows);
        if(result.retry) previous.delete(head);
        else previous.set(head,signature);
      }
      for(const head of previous.keys()) if(!groups.has(head)) previous.delete(head);
    } catch(error) {
      console.error('FAIL: '+error.message);
    }
    await sleep(60000);
  }
}
async function main() {
  const [mode,value]=process.argv.slice(2);
  if(mode==='plan') {
    console.log(JSON.stringify(rulesetPlan(value),null,2));
    return;
  }
  if(mode==='audit-rules') {
    const token=process.env.GOVERNANCE_RULES_AUDIT_TOKEN;
    if(!token||!process.argv[4]) throw new Error('缺少管理员规则读取令牌或输出文件');
    const record=await auditRules(new GitHub(async()=>token),positive(value,'App ID'));
    fs.writeFileSync(process.argv[4],JSON.stringify(record,null,2)+'\n',{mode:0o600});
    console.log('PASS: 当前完整规则版本已读取');
    return;
  }
  if(!['check','watch','merge'].includes(mode)) throw new Error('用法：trusted_admission.cjs plan <App ID> | audit-rules <App ID> <文件> | check <PR> | watch | merge <PR>');
  const client=await installationClient();
  if(mode==='watch') return watch(client);
  if(mode==='merge') {
    console.log(JSON.stringify(await merge(client,value)));
    return;
  }
  const number=positive(value,'PR 编号');
  const entry=[...(await openGroups(client)).entries()].find(([,rows])=>rows.some(pr=>pr.number===number));
  if(!entry) throw new Error('未找到受支持的开放 PR');
  const result=await publishGroup(client,...entry);
  if(result.errors.length) throw new Error(result.errors.join('\n'));
  console.log('PASS: 独立 App 已完成当前 PR 校验');
}
module.exports={
  REPOSITORY,REPOSITORY_ID,CHECK,TARGETS,GitHub,installationClient,supported,
  fingerprint,squashMessage,rulesetPlan,evaluate,openGroups,publishGroup,
  validBaseline,validUpdateGate,completeRuleset,auditRules,requireProtection,merge,verifyMerge,watch
};
if(require.main===module) main().catch(error=>{console.error('FAIL: '+error.message);process.exitCode=1;});
