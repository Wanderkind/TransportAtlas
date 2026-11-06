import {useCallback,useEffect,useRef,useState} from 'react';
import {getJSON} from './api';

type Result<T>={key:string|null;url:string|null;status:'idle'|'loading'|'ready'|'error';data:T|null;error:string|null;startedAt:number|null;receivedAt:number|null};
const pending=<T>(key:string|null,url:string|null):Result<T>=>({key,url,status:url?'loading':'idle',data:null,error:null,startedAt:null,receivedAt:null});

/** Exact-key results only: a render for a new identity never exposes old data. */
export function useKeyedRequest<T>(url:string|null,key:string|null,delayMs=0){
 const [state,setState]=useState<Result<T>>(()=>pending(key,url));
 const [attempt,setAttempt]=useState(0);
 const cache=useRef(new Map<string,Result<T>>());
 useEffect(()=>{
   if(!url||!key){setState(pending(key,null));return}
   const hit=cache.current.get(key);
   if(hit){setState(hit);return}
   let disposed=false;
   const controller=new AbortController();
   setState(pending(key,url));
   const timer=window.setTimeout(()=>{
     const startedAt=performance.now();
     getJSON<T>(url,controller.signal).then(data=>{
       if(disposed||controller.signal.aborted)return;
       const result:Result<T>={key,url,status:'ready',data,error:null,startedAt,receivedAt:performance.now()};
       cache.current.delete(key);cache.current.set(key,result);
       if(cache.current.size>4)cache.current.delete(cache.current.keys().next().value!);
       setState(result);
     }).catch(error=>{
       if(disposed||controller.signal.aborted)return;
       setState({key,url,status:'error',data:null,error:String(error),startedAt,receivedAt:performance.now()});
     });
   },delayMs);
   return ()=>{disposed=true;controller.abort();window.clearTimeout(timer)};
 },[url,key,attempt,delayMs]);
 const retry=useCallback(()=>{
   if(key)cache.current.delete(key);
   setState(pending(key,url));setAttempt(n=>n+1);
 },[key,url]);
 const current=!url?pending<T>(key,null):state.key===key?state:cache.current.get(key!)??pending<T>(key,url);
 return {...current,retry};
}
