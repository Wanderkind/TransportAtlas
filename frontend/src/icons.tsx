import type {SVGProps} from 'react';

function icon(path:string){
 return function Icon({size=16,...props}:SVGProps<SVGSVGElement>&{size?:number}){
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={path}/></svg>;
 };
}
export const MapPinned=icon('M3 5 9 3l6 2 6-2v16l-6 2-6-2-6 2Z M9 3v16 M15 5v16');
export const TrainFront=icon('M6 17h12V4H6Z M6 10h12 M9 20l-3 3 M15 20l3 3 M8 14h1 M15 14h1');
export const CalendarDays=icon('M3 5h18v16H3Z M3 10h18 M7 3v4 M17 3v4 M7 14h2 M14 14h2');
export const Database=icon('M3 6c0-4 18-4 18 0S3 10 3 6v12c0 4 18 4 18 0V6 M3 12c0 4 18 4 18 0');
export const Search=icon('M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0 M15 15l6 6');
export const Layers3=icon('M2 7l10-5 10 5-10 5Z M2 12l10 5 10-5 M2 17l10 5 10-5');
export const AlertTriangle=icon('M12 3 2 21h20Z M12 9v5 M12 17v1');
export const Clock3=icon('M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M12 6v6l4 2');
export const Map=MapPinned;
export const Maximize2=icon('M14 3h7v7 M21 3l-7 7 M3 14v7h7 M3 21l7-7');
export const WifiOff=icon('M2 2l20 20 M2 8c6-5 14-5 20 0 M5 12c4-3 10-3 14 0 M8 16c2-2 6-2 8 0 M12 20h.01');
