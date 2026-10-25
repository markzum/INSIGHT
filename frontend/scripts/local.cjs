// Local lifecycle commands. Only stop a process whose HTTP identity matches this project.
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const projectRoot = path.resolve(__dirname, '..');
const port = Number(process.env.PORT || 4173);
const origin = `http://127.0.0.1:${port}`;
const command = process.argv[2] || 'start';
async function status() {
  try {
    const response = await fetch(`${origin}/__local/status`,{signal:AbortSignal.timeout(1000)});
    if(!response.ok) return null;
    const identity = await response.json();
    return identity.app==='alfa-business-pulse' && identity.projectRoot===projectRoot && Number.isInteger(identity.pid) ? identity : null;
  } catch {return null;}
}
async function main() {
  if(!['start','status','stop'].includes(command)) throw new Error('Use: node scripts/local.cjs start|status|stop');
  let running = await status();
  if(command==='status') {console.log(running?`Running: ${origin} (PID ${running.pid})`:`Not running from ${projectRoot}`);return;}
  if(command==='stop') {
    if(!running){console.log('No matching project server is running.');return;}
    process.kill(running.pid);
    for(let attempt=0;attempt<20;attempt++){
      await new Promise(resolve=>setTimeout(resolve,100));
      if(!await status()){console.log(`Stopped project server (PID ${running.pid}).`);return;}
    }
    throw new Error('Stop was requested, but the server still responds.');
  }
  if(running){console.log(`Already running: ${origin} (PID ${running.pid})`);return;}
  const runtime=path.join(projectRoot,'.runtime');fs.mkdirSync(runtime,{recursive:true});
  const out=fs.openSync(path.join(runtime,'server.log'),'a');
  const err=fs.openSync(path.join(runtime,'server-error.log'),'a');
  const child=spawn(process.execPath,[path.join(__dirname,'serve.cjs')],{cwd:projectRoot,detached:true,windowsHide:true,stdio:['ignore',out,err],env:{...process.env,HOST:'127.0.0.1',PORT:String(port)}});
  let launchError;child.on('error',error=>{launchError=error;});child.unref();fs.closeSync(out);fs.closeSync(err);
  for(let attempt=0;attempt<20;attempt++){
    await new Promise(resolve=>setTimeout(resolve,200));
    if(launchError)throw launchError;
    running=await status();
    if(running){console.log(`Started: ${origin} (PID ${running.pid})\nProject: ${projectRoot}`);return;}
  }
  throw new Error(`Server did not start on port ${port}. Check .runtime/server-error.log; another application may be using this port.`);
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
