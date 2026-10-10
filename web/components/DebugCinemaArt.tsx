import styles from "./viewMenu.module.css";

/** Static 35mm machinery; the parent animates only the named groups. */
export function CinemaFrame({ id }: { id: string }) {
  const part = (name: string) => `${id}-${name}`;
  return (
    <svg className={styles.debugFrame} viewBox="0 0 240 88" preserveAspectRatio="none" fill="none" shapeRendering="crispEdges" aria-hidden="true">
      <defs>
        <linearGradient id={part("brass")} x1="0" y1="0" x2="0" y2="88" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--cinema-edge, #bab6a7)" /><stop offset=".14" stopColor="var(--cinema-metal, #77746a)" /><stop offset=".48" stopColor="var(--cinema-body, #3b372f)" /><stop offset=".84" stopColor="var(--cinema-metal, #77746a)" /><stop offset="1" stopColor="var(--cinema-edge, #bab6a7)" />
        </linearGradient>
        <linearGradient id={part("beam-left")} x1="25" y1="52" x2="190" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--cinema-hot, #fff0c4)" stopOpacity=".9" /><stop offset=".22" stopColor="var(--cinema-warm, #d5a368)" stopOpacity=".5" /><stop offset="1" stopColor="var(--cinema-warm, #d5a368)" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={part("beam-right")} x1="215" y1="52" x2="50" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--cinema-hot, #fff0c4)" stopOpacity=".9" /><stop offset=".22" stopColor="var(--cinema-warm, #d5a368)" stopOpacity=".5" /><stop offset="1" stopColor="var(--cinema-warm, #d5a368)" stopOpacity="0" />
        </linearGradient>
        <clipPath id={part("perimeter")}>
          <path d="M0 0H240V88H0Z M43 23H197V65H43Z" clipRule="evenodd" fillRule="evenodd" />
        </clipPath>
        <clipPath id={part("top-rail")}><path d="M44 5H196V18H44Z" /></clipPath>
        <clipPath id={part("bottom-rail")}><path d="M42 71H198V84H42Z" /></clipPath>
        <symbol id={part("screw")} viewBox="0 0 5 5">
          <path d="M1 0H4V1H5V4H4V5H1V4H0V1H1Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M1 1H4V4H1Z" fill="var(--cinema-metal, #77746a)" /><path d="M1 2H4V3H1Z" fill="var(--cinema-body, #3b372f)" />
        </symbol>
        <symbol id={part("perforation")} viewBox="0 0 12 13">
          <path d="M0 0H12V13H0Z" fill="var(--cinema-shadow, #0d0b09)" />
          <path d="M2 1H7V3H2Z M2 10H7V12H2Z" fill="var(--cinema-metal, #77746a)" />
          <path d="M2 1H7V2H2Z M2 10H7V11H2Z" fill="var(--cinema-edge, #bab6a7)" />
          <path d="M0 4H12V5H0Z M0 8H12V9H0Z" fill="var(--cinema-body, #3b372f)" />
          <path d="M10 5H11V8H10Z" fill="var(--cinema-metal, #77746a)" />
        </symbol>
        <symbol id={part("reel")} viewBox="-16 -16 32 32">
          <path d="M-6-16H6V-15H10V-13H13V-10H15V-6H16V6H15V10H13V13H10V15H6V16H-6V15H-10V13H-13V10H-15V6H-16V-6H-15V-10H-13V-13H-10V-15H-6Z" fill="var(--cinema-shadow, #0d0b09)" />
          <path d="M-5-14H5V-13H9V-11H11V-9H13V-5H14V5H13V9H11V11H9V13H5V14H-5V13H-9V11H-11V9H-13V5H-14V-5H-13V-9H-11V-11H-9V-13H-5Z" fill="var(--cinema-metal, #77746a)" />
          <path d="M-5-14H5V-13H-5Z M-9-13H-5V-12H-9Z M-13-9H-12V-5H-13Z M-14-5H-13V5H-14Z" fill="var(--cinema-shine, #ece8d8)" />
          <path d="M-5 13H5V14H-5Z M5 11H9V13H5Z M11 5H13V9H11Z M13-5H14V5H13Z" fill="var(--cinema-body, #3b372f)" />
          <path d="M-5-11H5V-9H9V-5H11V5H9V9H5V11H-5V9H-9V5H-11V-5H-9V-9H-5Z" fill="var(--cinema-metal, #77746a)" />
          <path d="M-2-10H2V-5H-2Z M5-8H8V-6H10V-3H5V-4H4V-6H5Z M5 3H10V6H8V8H5V6H4V4H5Z M-2 5H2V10H-2Z M-10 3H-5V4H-4V6H-5V8H-8V6H-10Z M-8-8H-5V-6H-4V-4H-5V-3H-10V-6H-8Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M-2-10H2V-9H-2Z M5-8H8V-7H5Z M-8-8H-5V-7H-8Z M-10 3H-9V6H-10Z M5 3H9V4H5Z M-2 5H-1V9H-2Z" fill="var(--cinema-edge, #bab6a7)" />
          <path d="M-2-3H2V-2H3V2H2V3H-2V2H-3V-2H-2Z" fill="var(--cinema-edge, #bab6a7)" />
          <path d="M-1-1H1V1H-1Z" fill="var(--cinema-body, #3b372f)" /><path d="M0-1H1V0H0Z" fill="var(--cinema-shine, #ece8d8)" />
        </symbol>
        <symbol id={part("still-mountain")} viewBox="0 0 24 13">
          {/* A moon and an improbable little rocket: the first movie dreams. */}
          <path d="M0 0H24V13H0Z" fill="var(--cinema-shadow, #0d0b09)" />
          <path d="M2 2H22V11H2Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M14 2H18V3H20V5H21V8H19V10H14V9H12V7H11V4H13V3H14Z M4 3H5V4H4Z M8 2H9V3H8Z" fill="var(--cinema-hot, #fff0c4)" />
          <path d="M17 4H19V5H17Z M18 7H20V8H18Z M14 8H16V9H14Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M5 9H7V8H8V7H10V6H12V5H14V4H16V5H15V6H13V7H11V8H9V9H7V10H5Z" fill="var(--cinema-warm, #d5a368)" />
          <path d="M3 10H5V11H3Z M8 7H10V8H8Z M11 5H13V6H11Z" fill="var(--cinema-hot, #fff0c4)" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="var(--cinema-edge, #bab6a7)" />
        </symbol>
        <symbol id={part("still-street")} viewBox="0 0 24 13">
          {/* One streetlamp, one silhouette, and all the stories in the dark. */}
          <path d="M0 0H24V13H0Z" fill="var(--cinema-shadow, #0d0b09)" />
          <path d="M2 2H22V11H2Z" fill="var(--cinema-film, #8d8874)" />
          <path d="M2 3H7V10H9V11H2Z M19 4H22V11H18V9H19Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M13 4H15V6H16V8H17V10H10V8H11V6H12V5H13Z" fill="var(--cinema-warm, #d5a368)" />
          <path d="M14 2H17V3H18V11H17V4H15V3H14Z M12 6H13V7H12Z M11 7H14V9H13V10H14V11H12V9H11V11H10V9H11Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M13 3H16V4H13Z M3 5H4V6H3Z M8 10H10V11H8Z M14 10H17V11H14Z" fill="var(--cinema-hot, #fff0c4)" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="var(--cinema-edge, #bab6a7)" />
        </symbol>
        <symbol id={part("still-closeup")} viewBox="0 0 24 13">
          {/* A western sunset, distant mesas, and a cactus holding the frame. */}
          <path d="M0 0H24V13H0Z" fill="var(--cinema-shadow, #0d0b09)" />
          <path d="M2 2H22V11H2Z" fill="var(--cinema-warm, #d5a368)" />
          <path d="M15 3H19V4H20V6H19V7H15V6H14V4H15Z" fill="var(--cinema-hot, #fff0c4)" />
          <path d="M2 8H9V7H12V5H15V7H17V8H19V7H22V11H2Z" fill="var(--cinema-film, #8d8874)" />
          <path d="M5 4H6V9H5Z M3 5H4V7H5V8H3Z M7 4H8V7H6V6H7Z M2 9H10V10H17V9H22V11H2Z" fill="var(--cinema-dark, #211e19)" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="var(--cinema-edge, #bab6a7)" />
        </symbol>
      </defs>

      {/* A bevelled projector chassis, with a quiet opening for the label. */}
      <path className={styles.cinemaChassis} fill="var(--cinema-frame, #191612)" d="M7 0H233V2H238V7H240V81H238V86H233V88H7V86H2V81H0V7H2V2H7Z" />
      <path d="M7 2H233V4H236V7H238V81H236V84H233V86H7V84H4V81H2V7H4V4H7Z M8 6V82H232V6Z" fill={`url(#${part("brass")})`} fillRule="evenodd" />
      <path d="M8 4H232V5H8Z M4 8H5V80H4Z" fill="var(--cinema-edge, #bab6a7)" fillOpacity=".6" />
      <path d="M8 83H232V84H8Z M235 8H236V80H235Z" fill="var(--cinema-shadow, #0d0b09)" />
      <path d="M42 21H198V23H42Z M42 65H198V67H42Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M45 23H195V24H45Z M45 64H195V65H45Z" fill="var(--cinema-dark, #211e19)" />
      <path d="M44 25H45V62H44Z M195 25H196V62H195Z" fill="var(--cinema-body, #3b372f)" fillOpacity=".65" />

      {/* The stock is clipped in its guides, so a short advance stays tidy. */}
      <path d="M43 4H197V19H43Z M41 70H199V85H41Z" fill="var(--cinema-shadow, #0d0b09)" />
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
          <use href={`#${part("still-closeup")}`} x="18" y="71" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="42" y="71" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="66" y="71" width="24" height="13" />
          <use href={`#${part("still-closeup")}`} x="90" y="71" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="114" y="71" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="138" y="71" width="24" height="13" />
          <use href={`#${part("still-closeup")}`} x="162" y="71" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="186" y="71" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="210" y="71" width="24" height="13" />
          <use href={`#${part("still-closeup")}`} x="234" y="71" width="24" height="13" />
          <use href={`#${part("still-mountain")}`} x="258" y="71" width="24" height="13" />
          <use href={`#${part("still-street")}`} x="282" y="71" width="24" height="13" />
        </g>
      </g>

      {/* The little hinged gate sits in the upper film rail, leaving the
          title its own quiet window instead of another row of decoration. */}
      <path d="M107 3H133V23H107Z" fill="var(--cinema-frame, #191612)" />
      <path d="M106 4H107V21H106Z M133 4H134V21H133Z M109 22H131V23H109Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M105 6H106V9H105Z M134 6H135V9H134Z" fill="var(--cinema-edge, #bab6a7)" />
      <g transform="translate(108 5)"><CinemaSlate /></g>

      {/* Threaded film paths and brass guide rollers connect the two reels. */}
      <path d="M9 39H12V60H17V68H39V71H14V64H9Z M228 39H231V64H226V71H201V68H223V60H228Z" fill="var(--cinema-shadow, #0d0b09)" />
      <path d="M12 40H14V59H18V66H38V68H16V62H12Z M226 40H228V62H224V68H202V66H222V59H226Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M16 40H33V42H35V61H32V64H21V61H18V53H16Z M207 40H224V53H222V61H219V64H208V61H205V42H207Z" fill="var(--cinema-body, #3b372f)" />
      <path d="M20 42H32V44H20Z M208 42H220V44H208Z M21 60H32V62H21Z M208 60H219V62H208Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M21 46H30V48H21Z M21 50H30V52H21Z M21 54H30V56H21Z M210 46H219V48H210Z M210 50H219V52H210Z M210 54H219V56H210Z" fill="var(--cinema-shadow, #0d0b09)" />
      <path d="M21 48H30V49H21Z M21 52H30V53H21Z M21 56H30V57H21Z M210 48H219V49H210Z M210 52H219V53H210Z M210 56H219V57H210Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M33 45H37V48H40V56H37V59H33Z M203 45H207V59H203V56H200V48H203Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M37 49H40V55H37Z M200 49H203V55H200Z" fill="var(--cinema-warm, #d5a368)" />
      <path d="M35 46H37V48H35Z M203 46H205V48H203Z" fill="var(--cinema-shine, #ece8d8)" />

      <g transform="translate(24 25)"><g className={styles.reelLeft}><use href={`#${part("reel")}`} x="-16" y="-16" width="32" height="32" /></g></g>
      <g transform="translate(216 25)"><g className={styles.reelRight}><use href={`#${part("reel")}`} x="-16" y="-16" width="32" height="32" /></g></g>

      {/* Leader marks, cooling fins, rivets and the little inspection plate. */}
      <path d="M8 6H18V8H8Z M222 6H232V8H222Z M7 67H10V80H7Z M230 67H233V80H230Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M8 70H9V72H8Z M8 75H9V77H8Z M231 70H232V72H231Z M231 75H232V77H231Z" fill="var(--cinema-edge, #bab6a7)" />
      <path d="M15 75H35V82H15Z M205 75H225V82H205Z" fill="var(--cinema-dark, #211e19)" />
      <path d="M17 77H19V80H17Z M21 77H23V80H21Z M25 77H27V80H25Z M29 77H33V78H29Z M29 79H32V80H29Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M207 77H222V78H207Z M207 79H215V80H207Z M218 79H222V80H218Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M39 7H41V14H39Z M199 7H201V14H199Z M38 74H40V80H38Z M200 74H202V80H200Z" fill="var(--cinema-edge, #bab6a7)" />
      <use href={`#${part("screw")}`} x="6" y="56" width="5" height="5" />
      <use href={`#${part("screw")}`} x="229" y="56" width="5" height="5" />
      <use href={`#${part("screw")}`} x="34" y="64" width="5" height="5" />
      <use href={`#${part("screw")}`} x="201" y="64" width="5" height="5" />

      <g clipPath={`url(#${part("perimeter")})`}>
        <g className={styles.standbyBeam}>
          <path d="M38 49L102 20H120L39 53Z M38 53L133 68H112L37 56Z" fill={`url(#${part("beam-left")})`} />
          <path d="M202 49L138 20H120L201 53Z M202 53L107 68H128L203 56Z" fill={`url(#${part("beam-right")})`} />
        </g>
        <g className={styles.beamLeft} fill={`url(#${part("beam-left")})`}>
          <path d="M38 47L152 3H211L39 54Z" /><path d="M38 52L187 85H111L37 56Z" />
          <path d="M37 49L197 11V13L39 52Z M38 54L174 79V81L37 56Z" fillOpacity=".6" />
        </g>
        <g className={styles.beamRight} fill={`url(#${part("beam-right")})`}>
          <path d="M202 47L88 3H29L201 54Z" /><path d="M202 52L53 85H129L203 56Z" />
          <path d="M203 49L43 11V13L201 52Z M202 54L66 79V81L203 56Z" fillOpacity=".6" />
        </g>
        <g className={styles.lampLight}>
          <path d="M36 47H40V48H41V56H40V57H36V56H35V48H36Z M200 47H204V48H205V56H204V57H200V56H199V48H200Z" fill="var(--cinema-warm, #d5a368)" />
          <path d="M37 48H40V56H37Z M200 48H203V56H200Z" fill="var(--cinema-hot, #fff0c4)" />
          <path d="M38 49H40V54H38Z M200 49H202V54H200Z" fill="var(--cinema-shine, #ece8d8)" />
        </g>
        {/* Closed blades cover the aperture; the parent opens each group
            around its local origin without moving the projector housing. */}
        <g transform="translate(38 52)">
          <g className={styles.shutterLeft}>
            <path d="M-3-5H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-dark, #211e19)" />
            <path d="M3 5H-3V2H-1V0H1V-2H3Z" fill="var(--cinema-body, #3b372f)" />
            <path d="M-3 1H-1V-1H1V-3H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-metal, #77746a)" />
          </g>
        </g>
        <g transform="translate(202 52)">
          <g className={styles.shutterRight}>
            <path d="M-3-5H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-dark, #211e19)" />
            <path d="M3 5H-3V2H-1V0H1V-2H3Z" fill="var(--cinema-body, #3b372f)" />
            <path d="M-3 1H-1V-1H1V-3H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-metal, #77746a)" />
          </g>
        </g>
        <path className={styles.powerTrim} d="M47 21H193V22H198V26H199V62H198V66H193V67H47V66H42V62H41V26H42V22H47Z"
          stroke="var(--cinema-power, #ead6a7)" strokeWidth="1" />
      </g>
    </svg>
  );
}

/** A real 24×17 gate, with its arm hinged at local (2, 6). */
export function CinemaSlate() {
  return (
    <svg className={styles.debugCrest} width="24" height="17" viewBox="0 0 24 17" fill="none" shapeRendering="crispEdges" aria-hidden="true">
      <path d="M1 6H23V16H22V17H2V16H1Z" fill="var(--cinema-shadow, #0d0b09)" />
      <path d="M2 7H22V16H2Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M3 9H21V15H3Z" fill="var(--cinema-shadow, #0d0b09)" />
      <path d="M2 7H22V9H2Z" fill="var(--cinema-edge, #bab6a7)" />
      <path d="M4 7H7V9H4Z M11 7H14V9H11Z M18 7H21V9H18Z" fill="var(--cinema-dark, #211e19)" />
      <path d="M4 10H9V11H4Z M12 10H20V11H12Z M4 13H20V14H4Z" fill="var(--cinema-edge, #bab6a7)" />
      <path d="M4 12H6V13H4Z M8 12H9V13H8Z M12 12H15V13H12Z M17 12H19V13H17Z" fill="var(--cinema-metal, #77746a)" />
      <path d="M10 10H11V14H10Z M3 15H21V16H3Z" fill="var(--cinema-body, #3b372f)" />
      <g className={styles.slateHinge}>
        <path d="M1 0H23V1H24V6H0V1H1Z" fill="var(--cinema-shadow, #0d0b09)" />
        <path d="M1 1H23V5H1Z" fill="var(--cinema-shine, #ece8d8)" />
        <path d="M5 1H10L6 5H1Z M15 1H20L16 5H11Z M23 3V5H21Z" fill="var(--cinema-dark, #211e19)" />
        <path d="M1 5H23V6H1Z" fill="var(--cinema-metal, #77746a)" />
      </g>
      <path d="M1 5H4V8H1Z" fill="var(--cinema-metal, #77746a)" /><path d="M2 6H3V7H2Z" fill="var(--cinema-dark, #211e19)" />
      <path d="M21 14H22V15H21Z M2 14H3V15H2Z" fill="var(--cinema-edge, #bab6a7)" />
    </svg>
  );
}
