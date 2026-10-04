import {BUILDABLE,inBounds} from './blocks.js';
import {GENERATOR_VERSION} from './generation.js';
export const DEFAULT_SETTINGS={viewDistance:3,sensitivity:1,volume:.25,pixelRatio:1.5};
export const MAX_IMPORT_BYTES=20*1024*1024;
function requireSave(ok,detail){if(!ok)throw new Error(`存档无效：${detail}`);}
export function validateSave(s) {
  requireSave(s&&typeof s==='object','不是对象');
  requireSave(s.formatVersion===1 && s.generatorVersion===GENERATOR_VERSION,'不支持的版本');
  requireSave(typeof s.seed==='string'&&s.seed.trim().length>0&&s.seed.length<=128,'世界种子');
  requireSave(typeof s.savedAt==='string'&&Number.isFinite(Date.parse(s.savedAt)),'保存时间');
  const p=s.player?.position;
  requireSave(p&&['x','y','z'].every(a=>Number.isFinite(p[a]))&&p.x>=-255.7&&p.x<=255.7&&p.z>=-255.7&&p.z<=255.7&&p.y>=1&&p.y<=94,'玩家坐标');
  requireSave(Number.isFinite(s.player.yaw)&&Math.abs(s.player.yaw)<=1e6&&Number.isFinite(s.player.pitch)&&Math.abs(s.player.pitch)<=1.5&&typeof s.player.flying==='boolean','玩家视角或模式');
  requireSave(Array.isArray(s.slots)&&s.slots.length===9&&s.slots.every(id=>BUILDABLE.includes(id)),'快捷栏');
  requireSave(Number.isInteger(s.selected)&&s.selected>=0&&s.selected<9,'选中槽位');
  const c=s.settings;
  requireSave(c&&[2,3,4].includes(c.viewDistance)&&Number.isFinite(c.sensitivity)&&c.sensitivity>=.5&&c.sensitivity<=2&&Number.isFinite(c.volume)&&c.volume>=0&&c.volume<=1&&[1,1.5,2].includes(c.pixelRatio),'设置');
  requireSave(Array.isArray(s.edits)&&s.edits.length<=400000,'修改记录大小');
  const seen=new Set();
  for(const entry of s.edits) {
    requireSave(Array.isArray(entry)&&entry.length===2&&typeof entry[0]==='string'&&/^-?\d+,-?\d+,-?\d+$/.test(entry[0]),'修改记录格式');
    const [x,y,z]=entry[0].split(',').map(Number);
    requireSave(inBounds(x,y,z)&&y>0&&`${x},${y},${z}`===entry[0]&&(entry[1]===0||BUILDABLE.includes(entry[1]))&&!seen.has(entry[0]),'方块、坐标或重复记录');
    seen.add(entry[0]);
  }
  // 只保留协议字段；外部 JSON 的额外字段不能进入运行状态。
  return structuredClone({formatVersion:1,generatorVersion:GENERATOR_VERSION,seed:s.seed,savedAt:s.savedAt,player:{position:p,yaw:s.player.yaw,pitch:s.player.pitch,flying:s.player.flying},slots:s.slots,selected:s.selected,settings:c,edits:s.edits});
}
export function createSnapshot(game) {
  return validateSave({formatVersion:1,generatorVersion:GENERATOR_VERSION,seed:game.world.seed,savedAt:new Date().toISOString(),player:{position:{...game.player.position},yaw:game.yaw,pitch:game.pitch,flying:game.player.flying},slots:[...game.slots],selected:game.selected,settings:{...game.settings},edits:Array.from(game.world.edits,entry=>[...entry])});
}
export class SaveQueue {
  constructor(write){this.write=write;this.tail=Promise.resolve();}
  enqueue(snapshot){const copy=structuredClone(snapshot),result=this.tail.then(()=>this.write(copy));this.tail=result.catch(()=>{});return result;}
}
export class BrowserStorage {
  async open() {
    if(this.db)return this.db;
    if(this.pending)return this.pending;
    this.pending=new Promise((resolve,reject)=>{
      if(!globalThis.indexedDB){reject(new Error('浏览器不支持本地存档'));return;}
      const request=indexedDB.open('block-sandbox-v1',1);
      let expired=false;
      const timer=setTimeout(()=>{expired=true;reject(new Error('存档打开超时，请关闭其他游戏标签页后重试'));},7000);
      request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains('saves'))request.result.createObjectStore('saves');};
      request.onsuccess=()=>{clearTimeout(timer);if(expired){request.result.close();return;}this.db=request.result;this.db.onversionchange=()=>{this.db.close();this.db=null;};resolve(this.db);};
      request.onerror=()=>{clearTimeout(timer);reject(request.error??new Error('无法打开本地存档'));};
      request.onblocked=()=>{clearTimeout(timer);expired=true;reject(new Error('存档被其他标签页占用'));};
    });
    try{return await this.pending;}finally{this.pending=null;}
  }
  async read() {
    const db=await this.open();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction('saves','readonly'),r=tx.objectStore('saves').get('current');
      r.onsuccess=()=>resolve(r.result??null);r.onerror=()=>reject(r.error);tx.onabort=()=>reject(tx.error);
    });
  }
  async write(snapshot) {
    const db=await this.open();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction('saves','readwrite');tx.objectStore('saves').put(snapshot,'current');
      tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error??new Error('存档写入失败'));tx.onerror=()=>{};
    });
  }
}
