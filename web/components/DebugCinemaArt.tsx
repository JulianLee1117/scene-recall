import styles from "./viewMenu.module.css";

/** Static 35mm machinery; the parent animates only the named groups. */
export function CinemaFrame({ id }: { id: string }) {
  const part = (name: string) => `${id}-${name}`;
  return (
    <svg className={styles.debugFrame} viewBox="0 0 240 104" preserveAspectRatio="none" fill="none" shapeRendering="crispEdges" aria-hidden="true">
      <defs>
        <linearGradient id={part("brass")} x1="0" y1="0" x2="0" y2="104" gradientUnits="userSpaceOnUse">
          <stop stopColor="#bc9a61" /><stop offset=".14" stopColor="#63553c" /><stop offset=".48" stopColor="#303d38" /><stop offset=".84" stopColor="#786440" /><stop offset="1" stopColor="#c0a06a" />
        </linearGradient>
        <linearGradient id={part("beam-left")} x1="25" y1="52" x2="190" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="#fff4c5" stopOpacity=".9" /><stop offset=".22" stopColor="#ecc481" stopOpacity=".5" /><stop offset="1" stopColor="#da9860" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={part("beam-right")} x1="215" y1="52" x2="50" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="#fff4c5" stopOpacity=".9" /><stop offset=".22" stopColor="#ecc481" stopOpacity=".5" /><stop offset="1" stopColor="#da9860" stopOpacity="0" />
        </linearGradient>
        <clipPath id={part("perimeter")}>
          <path d="M0 0H240V104H0Z M43 21H197V83H43Z" clipRule="evenodd" fillRule="evenodd" />
        </clipPath>
        <clipPath id={part("top-rail")}><path d="M44 5H196V18H44Z" /></clipPath>
        <clipPath id={part("bottom-rail")}><path d="M42 87H198V100H42Z" /></clipPath>
        <symbol id={part("screw")} viewBox="0 0 5 5">
          <path d="M1 0H4V1H5V4H4V5H1V4H0V1H1Z" fill="#22332f" />
          <path d="M1 1H4V4H1Z" fill="#9f9770" /><path d="M1 2H4V3H1Z" fill="#354640" />
        </symbol>
        <symbol id={part("perforation")} viewBox="0 0 12 13">
          <path d="M0 0H12V13H0Z" fill="#101a17" />
          <path d="M2 1H7V3H2Z M2 10H7V12H2Z" fill="#9e9771" />
          <path d="M2 1H7V2H2Z M2 10H7V11H2Z" fill="#d1ba82" />
          <path d="M0 4H12V5H0Z M0 8H12V9H0Z" fill="#4d5039" />
          <path d="M10 5H11V8H10Z" fill="#917244" />
        </symbol>
        <symbol id={part("reel")} viewBox="-19 -19 38 38">
          <path d="M-8-19H8V-17H13V-14H16V-10H18V-5H19V5H18V10H16V14H13V17H8V19H-8V17H-13V14H-16V10H-18V5H-19V-5H-18V-10H-16V-14H-13V-17H-8Z" fill="#101b19" />
          <path d="M-7-17H7V-15H12V-12H15V-7H17V7H15V12H12V15H7V17H-7V15H-12V12H-15V7H-17V-7H-15V-12H-12V-15H-7Z" fill="#9b9873" />
          <path d="M-7-17H7V-16H-7Z M-12-15H-7V-14H-12Z M-15-12H-12V-11H-15Z M-17-7H-16V7H-17Z" fill="#d9cd9a" />
          <path d="M-7 15H7V17H-7Z M7 13H12V15H7Z M12 10H15V12H12Z M15-7H17V7H15Z" fill="#5c6b54" />
          <path d="M-6-13H6V-11H10V-7H12V7H10V11H6V13H-6V11H-10V7H-12V-7H-10V-11H-6Z" fill="#73846b" />
          <path d="M-3-12H3V-7H2V-5H-2V-7H-3Z M6-9H9V-7H11V-3H6V-4H4V-7H6Z M6 3H11V7H9V9H6V7H4V4H6Z M-3 7H-2V5H2V7H3V12H-3Z M-11 3H-6V4H-4V7H-6V9H-9V7H-11Z M-9-9H-6V-7H-4V-4H-6V-3H-11V-7H-9Z" fill="#152723" />
          <path d="M-2-12H2V-11H-2Z M6-9H9V-8H6Z M-9-9H-6V-8H-9Z M-11 3H-10V7H-11Z M6 3H10V4H6Z M-3 7H-2V11H-3Z" fill="#d0c28c" />
          <path d="M-3-4H3V-3H4V3H3V4H-3V3H-4V-3H-3Z" fill="#d0bc80" />
          <path d="M-2-2H2V2H-2Z" fill="#33483d" /><path d="M-1-1H1V1H-1Z" fill="#eee0ae" />
        </symbol>
        <symbol id={part("still-mountain")} viewBox="0 0 24 13">
          <path d="M0 0H24V13H0Z" fill="#121c19" />
          <path d="M2 2H22V11H2Z" fill="#78948a" /><path d="M16 3H19V5H16Z" fill="#e7c784" />
          <path d="M2 9H4V7H7V5H9V7H12V9H15V8H18V10H22V11H2Z" fill="#314f49" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="#ae9d6e" />
        </symbol>
        <symbol id={part("still-street")} viewBox="0 0 24 13">
          <path d="M0 0H24V13H0Z" fill="#121c19" />
          <path d="M2 2H22V11H2Z" fill="#96724e" />
          <path d="M2 3H7V11H2Z M17 5H22V11H17Z M9 8H12V11H9Z M13 9H17V11H13Z" fill="#273e37" />
          <path d="M3 4H5V6H3Z M18 6H20V8H18Z M10 3H15V5H10Z M12 6H13V10H12Z" fill="#e4c993" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="#ae9d6e" />
        </symbol>
        <symbol id={part("still-closeup")} viewBox="0 0 24 13">
          <path d="M0 0H24V13H0Z" fill="#121c19" />
          <path d="M2 2H22V11H2Z" fill="#597c73" />
          <path d="M10 3H15V4H16V8H15V9H17V11H7V9H10V8H9V4H10Z" fill="#d6b77c" />
          <path d="M10 3H15V4H16V5H13V4H10Z M12 6H14V7H12Z M7 10H17V11H7Z" fill="#29433d" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="#ae9d6e" />
        </symbol>
      </defs>

      {/* A bevelled projector chassis, with a quiet opening for the label. */}
      <path className={styles.cinemaChassis} d="M7 0H233V2H238V7H240V97H238V102H233V104H7V102H2V97H0V7H2V2H7Z" />
      <path d="M7 2H233V4H236V7H238V97H236V100H233V102H7V100H4V97H2V7H4V4H7Z M8 6V98H232V6Z" fill={`url(#${part("brass")})`} fillRule="evenodd" />
      <path d="M8 4H232V5H8Z M4 8H5V96H4Z" fill="#d4bc83" fillOpacity=".6" />
      <path d="M8 99H232V100H8Z M235 8H236V96H235Z" fill="#0b1513" />
      <path d="M42 19H198V21H42Z M42 83H198V85H42Z" fill="#77653f" />
      <path d="M45 21H195V22H45Z M45 82H195V83H45Z" fill="#172a25" />
      <path d="M44 23H45V80H44Z M195 23H196V80H195Z" fill="#385047" fillOpacity=".65" />

      {/* The stock is clipped in its guides, so a short advance stays tidy. */}
      <path d="M43 4H197V19H43Z M41 86H199V101H41Z" fill="#080f0d" />
      <g clipPath={`url(#${part("top-rail")})`}>
        <g className={styles.filmStripTop}>
          <use href={`#${part("perforation")}`} x="32" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="44" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="56" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="68" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="80" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="92" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="104" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="116" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="128" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="140" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="152" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="164" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="176" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="188" y="5" width="12" height="13" />
          <use href={`#${part("perforation")}`} x="200" y="5" width="12" height="13" />
        </g>
      </g>
      <g clipPath={`url(#${part("bottom-rail")})`}>
        <g className={styles.filmStripBottom}>
          <use href={`#${part("still-closeup")}`} x="18" y="87" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="42" y="87" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="66" y="87" width="24" height="13" />
          <use href={`#${part("still-closeup")}`} x="90" y="87" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="114" y="87" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="138" y="87" width="24" height="13" />
          <use href={`#${part("still-closeup")}`} x="162" y="87" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="186" y="87" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="210" y="87" width="24" height="13" />
          <use href={`#${part("still-closeup")}`} x="234" y="87" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="258" y="87" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="282" y="87" width="24" height="13" />
        </g>
      </g>

      {/* Threaded film paths and brass guide rollers connect the two reels. */}
      <path d="M9 43H12V72H17V84H39V87H14V76H9Z M228 43H231V76H226V87H201V84H223V72H228Z" fill="#09120f" />
      <path d="M12 47H14V71H18V82H38V84H16V74H12Z M226 47H228V74H224V84H202V82H222V71H226Z" fill="#6f7350" />
      <path d="M16 46H34V49H37V73H34V76H21V73H18V63H16Z M206 46H224V63H222V73H219V76H206V73H203V49H206Z" fill="#2b4035" />
      <path d="M20 48H33V50H20Z M207 48H220V50H207Z M20 71H34V73H20Z M206 71H220V73H206Z" fill="#657655" />
      <path d="M21 53H31V55H21Z M21 58H31V60H21Z M21 63H31V65H21Z M209 53H219V55H209Z M209 58H219V60H209Z M209 63H219V65H209Z" fill="#101f19" />
      <path d="M21 55H31V56H21Z M21 60H31V61H21Z M21 65H31V66H21Z M209 55H219V56H209Z M209 60H219V61H209Z M209 65H219V66H209Z" fill="#7e825a" />
      <path d="M33 52H37V56H40V64H37V68H33Z M203 52H207V68H203V64H200V56H203Z" fill="#ab8c56" />
      <path d="M37 57H40V63H37Z M200 57H203V63H200Z" fill="#e9c582" />
      <path d="M35 53H37V55H35Z M203 53H205V55H203Z" fill="#efdaa2" />

      <g transform="translate(24 28)"><g className={styles.reelLeft}><use href={`#${part("reel")}`} x="-19" y="-19" width="38" height="38" /></g></g>
      <g transform="translate(216 28)"><g className={styles.reelRight}><use href={`#${part("reel")}`} x="-19" y="-19" width="38" height="38" /></g></g>

      {/* Leader marks, cooling fins, rivets and the little inspection plate. */}
      <path d="M8 6H18V8H8Z M222 6H232V8H222Z M7 83H10V96H7Z M230 83H233V96H230Z" fill="#6f7655" />
      <path d="M8 86H9V88H8Z M8 91H9V93H8Z M231 86H232V88H231Z M231 91H232V93H231Z" fill="#d7b977" />
      <path d="M15 91H35V98H15Z M205 91H225V98H205Z" fill="#27382b" />
      <path d="M17 93H19V96H17Z M21 93H23V96H21Z M25 93H27V96H25Z M29 93H33V94H29Z M29 95H32V96H29Z" fill="#b5a16b" />
      <path d="M207 93H222V94H207Z M207 95H215V96H207Z M218 95H222V96H218Z" fill="#a08f5d" />
      <path d="M39 7H41V14H39Z M199 7H201V14H199Z M38 90H40V96H38Z M200 90H202V96H200Z" fill="#d0b173" />
      <use href={`#${part("screw")}`} x="6" y="72" width="5" height="5" />
      <use href={`#${part("screw")}`} x="229" y="72" width="5" height="5" />
      <use href={`#${part("screw")}`} x="34" y="78" width="5" height="5" />
      <use href={`#${part("screw")}`} x="201" y="78" width="5" height="5" />

      <g clipPath={`url(#${part("perimeter")})`}>
        <g className={styles.beamLeft} fill={`url(#${part("beam-left")})`}>
          <path d="M38 55L152 3H211L39 62Z" /><path d="M38 60L187 101H111L37 64Z" />
          <path d="M37 57L197 11V13L39 60Z M38 62L174 95V97L37 64Z" fillOpacity=".6" />
        </g>
        <g className={styles.beamRight} fill={`url(#${part("beam-right")})`}>
          <path d="M202 55L88 3H29L201 62Z" /><path d="M202 60L53 101H129L203 64Z" />
          <path d="M203 57L43 11V13L201 60Z M202 62L66 95V97L203 64Z" fillOpacity=".6" />
        </g>
      </g>
    </svg>
  );
}

/** The arm's hinge is at (3, 9), independent of the body below it. */
export function CinemaSlate() {
  return (
    <svg className={styles.debugCrest} width="34" height="24" viewBox="0 0 34 24" fill="none" shapeRendering="crispEdges" aria-hidden="true">
      <path d="M2 9H33V22H31V24H4V23H2Z" fill="#101e1a" />
      <path d="M3 10H32V22H3Z" fill="currentColor" />
      <path d="M4 12H31V21H4Z" fill="#14231d" />
      <path d="M4 10H31V12H4Z" fill="#d5cba8" />
      <path d="M5 10H9V12H5Z M14 10H18V12H14Z M23 10H27V12H23Z" fill="#26372d" />
      <path d="M6 14H14V15H6Z M17 14H29V15H17Z M6 18H29V19H6Z" fill="#8d9a79" />
      <path d="M6 16H8V17H6Z M10 16H13V17H10Z M17 16H22V17H17Z M24 16H28V17H24Z" fill="#d2c79f" />
      <path d="M15 13H16V20H15Z M4 21H31V22H4Z" fill="#354c3e" />
      <g className={styles.slateHinge}>
        <path d="M2 2H32V3H34V9H1V3H2Z" fill="#16241e" />
        <path d="M2 3H33V8H2Z" fill="#eadfb9" />
        <path d="M6 3H12L7 8H2V7Z M18 3H24L19 8H13Z M30 3H33V6L31 8H25Z" fill="#253a30" />
        <path d="M3 3H6V4H3Z M12 3H18V4H12Z M24 3H30V4H24Z" fill="#fff0c9" />
        <path d="M2 8H33V9H2Z" fill="#b8aa80" />
      </g>
      <path d="M1 8H5V11H1Z" fill="#99a58a" /><path d="M2 9H4V10H2Z" fill="#263d31" />
      <path d="M31 20H32V21H31Z M3 20H4V21H3Z" fill="#d8caa1" />
    </svg>
  );
}
