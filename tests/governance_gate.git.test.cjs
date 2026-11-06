'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const cp=require('node:child_process');
const gate=path.join(__dirname,'..','.github','scripts','governance_gate.cjs');
const body="## 背景与目标\n检查提交与 PR 元数据的真实格式。\n## 修改范围与实现\n通过合成 Git 历史验证提交和文件范围。\n## 兼容性与风险\n不更改现有页面接口，也不提供写入凭据。\n## 实际验证\n分别执行了合法消息、英文标题和过时 HEAD 反例。\n## 未完成事项\n远端强制规则和独立审查仍需要完成验收。";
function git(cwd,args){
  const result=cp.spawnSync('git',args,{cwd,encoding:'utf8',timeout:10000});
  assert.equal(result.status,0,result.stdout+result.stderr);
  return result.stdout.trim();
}
test('准确的 PR 合并候选读取全部 Git 提交且不需要 GitHub 写入令牌',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-pr-git-'));
  try{
    git(tmp,['init','-b','main']);
    git(tmp,['config','user.name','Synthetic Maintainer']);
    git(tmp,['config','user.email','synthetic@example.invalid']);
    fs.writeFileSync(path.join(tmp,'page.txt'),'baseline\n','utf8');
    git(tmp,['add','page.txt']);
    git(tmp,['commit','-m','docs(test): 新增合成测试仓库初始记录']);
    const priorBase=git(tmp,['rev-parse','HEAD']);
    fs.writeFileSync(path.join(tmp,'info.txt'),'main baseline\n','utf8');
    git(tmp,['add','info.txt']);
    git(tmp,['commit','-m','docs(test): 补充合成测试的基线说明']);
    const base=git(tmp,['rev-parse','HEAD']);
    git(tmp,['checkout','-b','fix/api-state']);
    fs.writeFileSync(path.join(tmp,'page.txt'),'changed\n','utf8');
    git(tmp,['add','page.txt']);
    git(tmp,['commit','-m','fix(api): 修正调用失败后的权限状态处理','-m','权限失败将终止本次请求，不再继续执行下游业务操作。回归验证受保护路径与既有正常流程。']);
    const head=git(tmp,['rev-parse','HEAD']);
    git(tmp,['checkout','main']);
    git(tmp,['merge','--no-ff','--no-edit','fix/api-state']);
    const file=path.join(tmp,'event.json');
    const event={repository:{full_name:'example/repo'},pull_request:{
      number:9,title:'fix(api): 修正调用失败后的权限状态处理',body,
      head:{sha:head,ref:'fix/api-state'},base:{sha:base,ref:'main'}
    }};
    const run=(payload)=>{
      fs.writeFileSync(file,JSON.stringify(payload),'utf8');
      return cp.spawnSync(process.execPath,[gate,'event-git'],{cwd:tmp,encoding:'utf8',timeout:10000,
        env:{...process.env,GITHUB_TOKEN:'',GITHUB_EVENT_PATH:file}});
    };
    const good=run(event);
    assert.equal(good.status,0,good.stdout+good.stderr);
    const badTitle=run({...event,pull_request:{...event.pull_request,title:'fix(api): fix auth errors'}});
    assert.equal(badTitle.status,1,badTitle.stdout+badTitle.stderr);
    const stale=run({...event,pull_request:{...event.pull_request,head:{...event.pull_request.head,sha:base}}});
    assert.equal(stale.status,1,stale.stdout+stale.stderr);
    const staleBase=run({...event,pull_request:{...event.pull_request,base:{...event.pull_request.base,sha:priorBase}}});
    assert.equal(staleBase.status,1,staleBase.stdout+staleBase.stderr);
    assert.match(staleBase.stderr,/PR BASE 与事件不一致/u);
    const newBase=git(tmp,['rev-parse','HEAD']);
    git(tmp,['checkout','-b','docs/trailing-unicode']);
    fs.writeFileSync(path.join(tmp,'unicode.txt'),'verify trailing title\n','utf8');
    git(tmp,['add','unicode.txt']);
    git(tmp,['commit','--cleanup=verbatim','-m','docs(test): 校验真实提交标题末尾特殊空格\u00A0']);
    const invalidHead=git(tmp,['rev-parse','HEAD']);
    const raw=cp.execFileSync('git',['cat-file','commit',invalidHead],{cwd:tmp,encoding:'utf8'});
    assert(raw.includes('特殊空格\u00A0\n'));
    git(tmp,['checkout','main']);
    git(tmp,['merge','--no-ff','--no-edit','docs/trailing-unicode']);
    const invalidEvent={...event,pull_request:{...event.pull_request,
      head:{sha:invalidHead,ref:'docs/trailing-unicode'},base:{sha:newBase,ref:'main'}
    }};
    const invalid=run(invalidEvent);
    assert.equal(invalid.status,1,invalid.stdout+invalid.stderr);
    assert.match(invalid.stderr,/非普通空格/u);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});


test('本地提交钩子执行 Git 私有目录中的可信校验器',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-'));
  try{
    git(tmp,['init','-b','main']);
    git(tmp,['config','user.name','Synthetic Maintainer']);
    git(tmp,['config','user.email','synthetic@example.invalid']);
    const scriptDir=path.join(tmp,'.github','scripts');
    fs.mkdirSync(scriptDir,{recursive:true});
    fs.copyFileSync(gate,path.join(scriptDir,'governance_gate.cjs'));
    git(tmp,['add','.github/scripts/governance_gate.cjs']);
    git(tmp,['commit','-m','docs(test): 建立合成可信提交检查入口']);
    const hooksDir=git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']);
    fs.mkdirSync(hooksDir,{recursive:true});
    const trusted=cp.execFileSync('git',['show','main:.github/scripts/governance_gate.cjs'],{cwd:tmp});
    fs.writeFileSync(path.join(hooksDir,'governance_gate.cjs'),trusted);
    const hook=['#!/bin/sh','set -eu',
      'hooks_dir="$(git rev-parse --path-format=absolute --git-path hooks)"',
      'exec node "$hooks_dir/governance_gate.cjs" message-file "$1"',''].join('\n');
    const hookPath=path.join(hooksDir,'commit-msg');
    fs.writeFileSync(hookPath,hook,{mode:0o755});
    fs.chmodSync(hookPath,0o755);
    git(tmp,['config','--local','core.hooksPath',hooksDir]);
    git(tmp,['checkout','-b','fix/untrusted-branch']);
    fs.writeFileSync(path.join(scriptDir,'governance_gate.cjs'),"process.exit(0);\n");
    const worktreeHooks=path.join(tmp,'.githooks');
    fs.mkdirSync(worktreeHooks);
    fs.writeFileSync(path.join(worktreeHooks,'commit-msg'),'#!/bin/sh\nexit 0\n');
    git(tmp,['add','.github/scripts/governance_gate.cjs','.githooks/commit-msg']);
    const rejected=cp.spawnSync('git',['commit','-m','fix(api): fix auth errors'],{cwd:tmp,encoding:'utf8',timeout:10000});
    assert.notEqual(rejected.status,0,rejected.stdout+rejected.stderr);
    assert.match(rejected.stderr,/主题中文行为或对象描述不足/u);
    git(tmp,['commit','-m','docs(test): 确认可信钩子不执行工作区脚本']);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});

const installer=path.join(__dirname,'..','scripts','install-governance-hook.sh');
const shell=process.platform==='win32'
  ?path.join(process.env.ProgramFiles||'C:\\Program Files','Git','bin','bash.exe'):'sh';
function hookFixture(tmp){
  git(tmp,['init','-b','main']);
  git(tmp,['config','user.name','Synthetic Maintainer']);
  git(tmp,['config','user.email','synthetic@example.invalid']);
  const scriptDir=path.join(tmp,'.github','scripts');
  fs.mkdirSync(scriptDir,{recursive:true});
  fs.copyFileSync(gate,path.join(scriptDir,'governance_gate.cjs'));
  git(tmp,['add','.github/scripts/governance_gate.cjs']);
  git(tmp,['commit','-m','docs(test): 建立可信钩子安装测试基线']);
  git(tmp,['update-ref','refs/remotes/origin/main','HEAD']);
}
function runInstaller(cwd){
  return cp.spawnSync(shell,[installer],{cwd,encoding:'utf8',timeout:10000});
}
function rejectedCommit(cwd){
  fs.writeFileSync(path.join(cwd,'message-test.txt'),'changes\n','utf8');
  git(cwd,['add','message-test.txt']);
  return cp.spawnSync('git',['commit','-m','fix(api): fix auth errors'],{cwd,encoding:'utf8',timeout:10000});
}

test('Git 编辑器注释不充当正文且真实违规内容仍被拒绝',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-editor-'));
  try{
    hookFixture(tmp);
    const installed=runInstaller(tmp);
    assert.equal(installed.status,0,installed.stdout+installed.stderr);
    const title='fix(api): 修正编辑器提交校验中的注释处理';
    const explanation='提交校验需要排除 Git 自动生成的状态注释，并保留完整真实正文的格式与字符约束。';
    const input=path.join(tmp,'.editor-content');
    const editor=path.join(tmp,'.editor.sh');
    const target=path.join(tmp,'content.txt');
    fs.writeFileSync(editor,'#!/bin/sh\nset -eu\nfile="$1"\ncat .editor-content "$file" > "$file.next"\nmv "$file.next" "$file"\n','utf8');
    const env={...process.env};
    delete env.GIT_EDITOR;
    const commit=(args)=>cp.spawnSync('git',args,{cwd:tmp,encoding:'utf8',timeout:10000,env});
    const edit=['-c','core.editor=sh .editor.sh','commit'];
    fs.writeFileSync(input,title+'\n\n'+explanation+'\n\n','utf8');
    fs.writeFileSync(target,'initial content\n','utf8');
    git(tmp,['add','content.txt']);
    const accepted=commit(edit);
    assert.equal(accepted.status,0,accepted.stdout+accepted.stderr);
    const stored=git(tmp,['show','-s','--format=%B','HEAD']);
    assert.match(stored,/修正编辑器提交校验中的注释处理/u);
    assert.doesNotMatch(stored,/^#\t(?:new file|modified):/mu);

    fs.writeFileSync(target,'changed content\n','utf8');
    git(tmp,['add','content.txt']);
    fs.writeFileSync(input,title+'\n\n'+explanation+'\nTODO: 补充实际影响\n\n','utf8');
    const placeholder=commit(edit);
    assert.notEqual(placeholder.status,0,placeholder.stdout+placeholder.stderr);
    assert.match(placeholder.stderr,/模板占位/u);

    fs.writeFileSync(input,title+'\n\n'+explanation+'\n\n','utf8');
    const scissors=commit(['-c','commit.cleanup=scissors',...edit]);
    assert.equal(scissors.status,0,scissors.stdout+scissors.stderr);
    const scissorsMessage=git(tmp,['show','-s','--format=%B','HEAD']);
    assert.match(scissorsMessage,/修正编辑器提交校验中的注释处理/u);
    assert.doesNotMatch(scissorsMessage,/^#\t(?:new file|modified):/mu);
    fs.writeFileSync(target,'additional change\n','utf8');
    git(tmp,['add','content.txt']);

    const verbatim=commit(['-c','commit.cleanup=verbatim',...edit]);
    assert.notEqual(verbatim.status,0,verbatim.stdout+verbatim.stderr);
    assert.match(verbatim.stderr,/提交正文存在控制字符/u);

    const invisible=commit(['commit','-m',title,'-m',explanation+'\u200B']);
    assert.notEqual(invisible.status,0,invisible.stdout+invisible.stderr);
    assert.match(invisible.stderr,/不可见格式字符/u);
    const plain=commit(['commit','-m',title,'-m',explanation]);
    assert.equal(plain.status,0,plain.stdout+plain.stderr);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});

test('安装器从旧工作区 hooksPath 迁移到 Git 私有目录，且重复安装不执行工作区代码',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-install-'));
  try{
    hookFixture(tmp);
    git(tmp,['config','--local','core.hooksPath','.githooks']);
    const installed=runInstaller(tmp);
    assert.equal(installed.status,0,installed.stdout+installed.stderr);
    const gitDir=git(tmp,['rev-parse','--path-format=absolute','--git-common-dir']);
    const hooksDir=git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']);
    assert.equal(path.dirname(path.resolve(hooksDir)),path.resolve(gitDir));
    assert.match(path.basename(hooksDir),/^\.memeatlas-hooks\./u);
    assert.equal(fs.existsSync(path.join(hooksDir,'governance_gate.cjs')),true);
    const reinstalled=runInstaller(tmp);
    assert.equal(reinstalled.status,0,reinstalled.stdout+reinstalled.stderr);
    git(tmp,['checkout','-b','fix/untrusted-branch']);
    fs.writeFileSync(path.join(tmp,'.github','scripts','governance_gate.cjs'),'process.exit(0);\n');
    const untrusted=path.join(tmp,'.githooks');
    fs.mkdirSync(untrusted,{recursive:true});
    fs.writeFileSync(path.join(untrusted,'commit-msg'),'#!/bin/sh\nexit 0\n');
    git(tmp,['add','.github/scripts/governance_gate.cjs','.githooks/commit-msg']);
    const invalid=rejectedCommit(tmp);
    assert.notEqual(invalid.status,0,invalid.stdout+invalid.stderr);
    assert.match(invalid.stderr,/主题中文行为或对象描述不足/u);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});

test('安装器读取可信版本失败后保留既有可用钩子与校验器',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-failure-'));
  try{
    hookFixture(tmp);
    const initial=runInstaller(tmp);
    assert.equal(initial.status,0,initial.stdout+initial.stderr);
    const hooksDir=git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']);
    const original=fs.readFileSync(path.join(hooksDir,'governance_gate.cjs'),'utf8');
    git(tmp,['update-ref','-d','refs/remotes/origin/main']);
    const broken=runInstaller(tmp);
    assert.notEqual(broken.status,0,broken.stdout+broken.stderr);
    assert.equal(git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']),hooksDir);
    assert.equal(fs.readFileSync(path.join(hooksDir,'governance_gate.cjs'),'utf8'),original);
    const invalid=rejectedCommit(tmp);
    assert.notEqual(invalid.status,0,invalid.stdout+invalid.stderr);
    assert.match(invalid.stderr,/主题中文行为或对象描述不足/u);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});

test('安装器拒绝替换已有 commit-msg 钩子及旧工作区中的自定义钩子',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-conflict-'));
  try{
    hookFixture(tmp);
    const defaultHooks=git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']);
    const existing=path.join(defaultHooks,'commit-msg');
    fs.writeFileSync(existing,'#!/bin/sh\nexit 9\n',{mode:0o755});
    fs.chmodSync(existing,0o755);
    const conflict=runInstaller(tmp);
    assert.notEqual(conflict.status,0,conflict.stdout+conflict.stderr);
    assert.equal(fs.readFileSync(existing,'utf8'),'#!/bin/sh\nexit 9\n');
    assert.equal(git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']),defaultHooks);
    fs.unlinkSync(existing);
    const untrusted=path.join(tmp,'.githooks');
    fs.mkdirSync(untrusted,{recursive:true});
    fs.writeFileSync(path.join(untrusted,'commit-msg'),'#!/bin/sh\nexit 9\n',{mode:0o755});
    git(tmp,['config','--local','core.hooksPath','.githooks']);
    const oldHook=runInstaller(tmp);
    assert.notEqual(oldHook.status,0,oldHook.stdout+oldHook.stderr);
    assert.equal(fs.readFileSync(path.join(untrusted,'commit-msg'),'utf8'),'#!/bin/sh\nexit 9\n');
    assert.match(git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']),/\.githooks$/u);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});


test('安装器遵循工作树级钩子配置并拒绝违规提交',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-worktree-'));
  try{
    hookFixture(tmp);
    git(tmp,['config','extensions.worktreeConfig','true']);
    git(tmp,['config','--local','core.hooksPath','.old-local-hooks']);
    git(tmp,['config','--worktree','core.hooksPath','.githooks']);
    const installed=runInstaller(tmp);
    assert.equal(installed.status,0,installed.stdout+installed.stderr);
    const gitDir=git(tmp,['rev-parse','--path-format=absolute','--git-common-dir']);
    const effective=git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']);
    assert.equal(path.dirname(path.resolve(effective)),path.resolve(gitDir));
    assert.match(path.basename(effective),/^\.memeatlas-hooks\./u);
    assert.equal(git(tmp,['config','--worktree','--get','core.hooksPath']),effective);
    assert.equal(git(tmp,['config','--local','--get','core.hooksPath']),'.old-local-hooks');
    git(tmp,['checkout','-b','fix/untrusted-worktree']);
    fs.writeFileSync(path.join(tmp,'.github','scripts','governance_gate.cjs'),'process.exit(0);\n');
    const untrusted=path.join(tmp,'.githooks');
    fs.mkdirSync(untrusted,{recursive:true});
    fs.writeFileSync(path.join(untrusted,'commit-msg'),'#!/bin/sh\nexit 0\n',{mode:0o755});
    fs.chmodSync(path.join(untrusted,'commit-msg'),0o755);
    git(tmp,['add','.github/scripts/governance_gate.cjs','.githooks/commit-msg']);
    const invalid=rejectedCommit(tmp);
    assert.notEqual(invalid.status,0,invalid.stdout+invalid.stderr);
    assert.match(invalid.stderr,/主题中文行为或对象描述不足/u);
    assert.equal(git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']),effective);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});

test('配置被更高优先级覆盖时安装器拒绝成功并恢复原配置',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-override-'));
  try{
    hookFixture(tmp);
    git(tmp,['config','--local','core.hooksPath','.previous-hooks']);
    const blocked=cp.spawnSync(shell,[installer],{cwd:tmp,encoding:'utf8',timeout:10000,env:{
      ...process.env,GIT_CONFIG_COUNT:'1',GIT_CONFIG_KEY_0:'core.hooksPath',GIT_CONFIG_VALUE_0:'.githooks'
    }});
    assert.notEqual(blocked.status,0,blocked.stdout+blocked.stderr);
    assert.match(blocked.stderr,/钩子路径未生效/u);
    assert.equal(git(tmp,['config','--local','--get','core.hooksPath']),'.previous-hooks');
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});

test('切换钩子期间收到终止信号仍保留生效的可信校验器',{
  skip:process.platform==='win32',
  timeout:10000
},async()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-hook-signal-'));
  try{
    hookFixture(tmp);
    const bin=path.join(tmp,'shim');
    fs.mkdirSync(bin);
    const marker=path.join(tmp,'git-config-written');
    const gitBinary=cp.execFileSync('sh',['-c','command -v git'],{encoding:'utf8'}).trim();
    const shim=[
      '#!/bin/sh',
      'if [ "$1" = config ] && { [ "$2" = --local ] || [ "$2" = --worktree ]; } && [ "$3" = core.hooksPath ] && [ "$#" -eq 4 ]; then',
      '  "$REAL_GIT" "$@" || exit $?',
      '  : > "$SIGNAL_MARKER"',
      '  sleep 1',
      '  exit 0',
      'fi',
      'exec "$REAL_GIT" "$@"',''
    ].join('\n');
    fs.writeFileSync(path.join(bin,'git'),shim,{mode:0o755});
    fs.chmodSync(path.join(bin,'git'),0o755);
    const child=cp.spawn(shell,[installer],{cwd:tmp,stdio:'ignore',env:{
      ...process.env,PATH:bin+path.delimiter+process.env.PATH,REAL_GIT:gitBinary,SIGNAL_MARKER:marker
    }});
    let signaled=false;
    const result=await new Promise((resolve,reject)=>{
      const timer=setInterval(()=>{
        if(fs.existsSync(marker)&&!signaled){signaled=true;child.kill('SIGTERM');}
      },10);
      child.once('error',error=>{clearInterval(timer);reject(error);});
      child.once('exit',(code,signal)=>{clearInterval(timer);resolve({code,signal});});
    });
    assert(signaled,'安装必须在提交配置后接收终止信号');
    assert.notEqual(result.code,0);
    const effective=git(tmp,['rev-parse','--path-format=absolute','--git-path','hooks']);
    assert.equal(fs.existsSync(path.join(effective,'governance_gate.cjs')),true);
    assert.equal(fs.existsSync(path.join(effective,'commit-msg')),true);
    const invalid=rejectedCommit(tmp);
    assert.notEqual(invalid.status,0,invalid.stdout+invalid.stderr);
    assert.match(invalid.stderr,/主题中文行为或对象描述不足/u);
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});


test('拉取可信主分支之前禁用旧工作区钩子',()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'governance-safe-fetch-'));
  try{
    const maintainer=path.join(tmp,'maintainer');
    const consumer=path.join(tmp,'consumer');
    const origin=path.join(tmp,'origin.git');
    fs.mkdirSync(maintainer);
    git(maintainer,['init','-b','main']);
    git(maintainer,['config','user.name','Synthetic Maintainer']);
    git(maintainer,['config','user.email','synthetic@example.invalid']);
    fs.writeFileSync(path.join(maintainer,'README.md'),'first\n');
    git(maintainer,['add','README.md']);
    git(maintainer,['commit','-m','docs(test): 建立可信拉取测试仓库']);
    git(tmp,['init','--bare',origin]);
    git(maintainer,['remote','add','origin',origin]);
    git(maintainer,['push','origin','main']);
    git(tmp,['clone','-b','main',origin,consumer]);

    const legacy=path.join(consumer,'.githooks');
    const marker=path.join(tmp,'legacy-hook-executed');
    fs.mkdirSync(legacy);
    fs.writeFileSync(path.join(legacy,'reference-transaction'),
      '#!/bin/sh\nprintf invoked > "'+marker+'"\n',{mode:0o755});
    fs.chmodSync(path.join(legacy,'reference-transaction'),0o755);
    git(consumer,['config','--local','core.hooksPath','.githooks']);

    const publish=content=>{
      fs.writeFileSync(path.join(maintainer,'README.md'),content);
      git(maintainer,['add','README.md']);
      git(maintainer,['commit','-m','docs(test): 更新远端内容用于拉取流程检查']);
      git(maintainer,['push','origin','main']);
    };
    publish('second\n');
    const guide=fs.readFileSync(path.join(__dirname,'..','governance','LOCAL-ENTRYPOINT.md'),'utf8');
    assert.match(guide,/git -c core\.hooksPath="\$safe_hooks" fetch origin main/u);
    const fetchScript=[
      'safe_hooks="$(mktemp -d)"',
      'trap \x27rm -rf -- "$safe_hooks"\x27 EXIT',
      'git -c core.hooksPath="$safe_hooks" fetch origin main'
    ].join('\n');
    const bash=process.platform==='win32'?shell:'bash';
    const safe=cp.spawnSync(bash,['-e','-o','pipefail','-c',fetchScript],
      {cwd:consumer,encoding:'utf8',timeout:10000});
    assert.equal(safe.status,0,safe.stdout+safe.stderr);
    assert.equal(fs.existsSync(marker),false,'可信拉取不得执行旧工作区钩子');
    assert.equal(git(consumer,['rev-parse','refs/remotes/origin/main']),git(maintainer,['rev-parse','HEAD']));

    publish('third\n');
    git(consumer,['fetch','origin','main']);
    assert.equal(fs.existsSync(marker),true,'对照拉取应能触发原有不可信钩子');
  }finally{fs.rmSync(tmp,{recursive:true,force:true});}
});
