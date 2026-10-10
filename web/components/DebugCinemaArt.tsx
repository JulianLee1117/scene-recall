import styles from "./viewMenu.module.css";

/** Static 35mm machinery; the parent animates only the named groups. */
export function CinemaFrame({ id }: { id: string }) {
  const part = (name: string) => `${id}-${name}`;
  return (
    <svg className={styles.debugFrame} viewBox="0 0 240 104" preserveAspectRatio="none" fill="none" shapeRendering="crispEdges" aria-hidden="true">
      <defs>
        <linearGradient id={part("brass")} x1="0" y1="0" x2="0" y2="104" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--cinema-edge, #a3bdc9)" /><stop offset=".14" stopColor="var(--cinema-metal, #667f8f)" /><stop offset=".48" stopColor="var(--cinema-body, #304451)" /><stop offset=".84" stopColor="var(--cinema-metal, #667f8f)" /><stop offset="1" stopColor="var(--cinema-edge, #a3bdc9)" />
        </linearGradient>
        <linearGradient id={part("beam-left")} x1="25" y1="52" x2="190" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--cinema-hot, #fff2c4)" stopOpacity=".9" /><stop offset=".22" stopColor="var(--cinema-warm, #e1ae67)" stopOpacity=".5" /><stop offset="1" stopColor="var(--cinema-warm, #e1ae67)" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={part("beam-right")} x1="215" y1="52" x2="50" y2="52" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--cinema-hot, #fff2c4)" stopOpacity=".9" /><stop offset=".22" stopColor="var(--cinema-warm, #e1ae67)" stopOpacity=".5" /><stop offset="1" stopColor="var(--cinema-warm, #e1ae67)" stopOpacity="0" />
        </linearGradient>
        <clipPath id={part("perimeter")}>
          <path d="M0 0H240V104H0Z M43 21H197V83H43Z" clipRule="evenodd" fillRule="evenodd" />
        </clipPath>
        <clipPath id={part("top-rail")}><path d="M44 5H196V18H44Z" /></clipPath>
        <clipPath id={part("bottom-rail")}><path d="M42 87H198V100H42Z" /></clipPath>
        <symbol id={part("screw")} viewBox="0 0 5 5">
          <path d="M1 0H4V1H5V4H4V5H1V4H0V1H1Z" fill="var(--cinema-dark, #162431)" />
          <path d="M1 1H4V4H1Z" fill="var(--cinema-metal, #667f8f)" /><path d="M1 2H4V3H1Z" fill="var(--cinema-body, #304451)" />
        </symbol>
        <symbol id={part("perforation")} viewBox="0 0 12 13">
          <path d="M0 0H12V13H0Z" fill="var(--cinema-shadow, #080f16)" />
          <path d="M2 1H7V3H2Z M2 10H7V12H2Z" fill="var(--cinema-metal, #667f8f)" />
          <path d="M2 1H7V2H2Z M2 10H7V11H2Z" fill="var(--cinema-edge, #a3bdc9)" />
          <path d="M0 4H12V5H0Z M0 8H12V9H0Z" fill="var(--cinema-body, #304451)" />
          <path d="M10 5H11V8H10Z" fill="var(--cinema-metal, #667f8f)" />
        </symbol>
        <symbol id={part("reel")} viewBox="-19 -19 38 38">
          <path d="M-8-19H8V-17H13V-14H16V-10H18V-5H19V5H18V10H16V14H13V17H8V19H-8V17H-13V14H-16V10H-18V5H-19V-5H-18V-10H-16V-14H-13V-17H-8Z" fill="var(--cinema-shadow, #080f16)" />
          <path d="M-7-17H7V-15H12V-12H15V-7H17V7H15V12H12V15H7V17H-7V15H-12V12H-15V7H-17V-7H-15V-12H-12V-15H-7Z" fill="var(--cinema-metal, #667f8f)" />
          <path d="M-7-17H7V-16H-7Z M-12-15H-7V-14H-12Z M-15-12H-12V-11H-15Z M-17-7H-16V7H-17Z" fill="var(--cinema-shine, #e7f3f6)" />
          <path d="M-7 15H7V17H-7Z M7 13H12V15H7Z M12 10H15V12H12Z M15-7H17V7H15Z" fill="var(--cinema-metal, #667f8f)" />
          <path d="M-6-13H6V-11H10V-7H12V7H10V11H6V13H-6V11H-10V7H-12V-7H-10V-11H-6Z" fill="var(--cinema-metal, #667f8f)" />
          <path d="M-3-12H3V-7H2V-5H-2V-7H-3Z M6-9H9V-7H11V-3H6V-4H4V-7H6Z M6 3H11V7H9V9H6V7H4V4H6Z M-3 7H-2V5H2V7H3V12H-3Z M-11 3H-6V4H-4V7H-6V9H-9V7H-11Z M-9-9H-6V-7H-4V-4H-6V-3H-11V-7H-9Z" fill="var(--cinema-dark, #162431)" />
          <path d="M-2-12H2V-11H-2Z M6-9H9V-8H6Z M-9-9H-6V-8H-9Z M-11 3H-10V7H-11Z M6 3H10V4H6Z M-3 7H-2V11H-3Z" fill="var(--cinema-edge, #a3bdc9)" />
          <path d="M-3-4H3V-3H4V3H3V4H-3V3H-4V-3H-3Z" fill="var(--cinema-edge, #a3bdc9)" />
          <path d="M-2-2H2V2H-2Z" fill="var(--cinema-body, #304451)" /><path d="M-1-1H1V1H-1Z" fill="var(--cinema-shine, #e7f3f6)" />
        </symbol>
        <symbol id={part("still-mountain")} viewBox="0 0 24 13">
          {/* A moon and an improbable little rocket: the first movie dreams. */}
          <path d="M0 0H24V13H0Z" fill="var(--cinema-shadow, #080f16)" />
          <path d="M2 2H22V11H2Z" fill="var(--cinema-dark, #162431)" />
          <path d="M14 2H18V3H20V5H21V8H19V10H14V9H12V7H11V4H13V3H14Z M4 3H5V4H4Z M8 2H9V3H8Z" fill="var(--cinema-hot, #fff2c4)" />
          <path d="M17 4H19V5H17Z M18 7H20V8H18Z M14 8H16V9H14Z" fill="var(--cinema-dark, #162431)" />
          <path d="M5 9H7V8H8V7H10V6H12V5H14V4H16V5H15V6H13V7H11V8H9V9H7V10H5Z" fill="var(--cinema-warm, #e1ae67)" />
          <path d="M3 10H5V11H3Z M8 7H10V8H8Z M11 5H13V6H11Z" fill="var(--cinema-hot, #fff2c4)" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="var(--cinema-edge, #a3bdc9)" />
        </symbol>
        <symbol id={part("still-street")} viewBox="0 0 24 13">
          {/* One streetlamp, one silhouette, and all the stories in the dark. */}
          <path d="M0 0H24V13H0Z" fill="var(--cinema-shadow, #080f16)" />
          <path d="M2 2H22V11H2Z" fill="var(--cinema-film, #7895a0)" />
          <path d="M2 3H7V10H9V11H2Z M19 4H22V11H18V9H19Z" fill="var(--cinema-dark, #162431)" />
          <path d="M13 4H15V6H16V8H17V10H10V8H11V6H12V5H13Z" fill="var(--cinema-warm, #e1ae67)" />
          <path d="M14 2H17V3H18V11H17V4H15V3H14Z M12 6H13V7H12Z M11 7H14V9H13V10H14V11H12V9H11V11H10V9H11Z" fill="var(--cinema-dark, #162431)" />
          <path d="M13 3H16V4H13Z M3 5H4V6H3Z M8 10H10V11H8Z M14 10H17V11H14Z" fill="var(--cinema-hot, #fff2c4)" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="var(--cinema-edge, #a3bdc9)" />
        </symbol>
        <symbol id={part("still-closeup")} viewBox="0 0 24 13">
          {/* A western sunset, distant mesas, and a cactus holding the frame. */}
          <path d="M0 0H24V13H0Z" fill="var(--cinema-shadow, #080f16)" />
          <path d="M2 2H22V11H2Z" fill="var(--cinema-warm, #e1ae67)" />
          <path d="M15 3H19V4H20V6H19V7H15V6H14V4H15Z" fill="var(--cinema-hot, #fff2c4)" />
          <path d="M2 8H9V7H12V5H15V7H17V8H19V7H22V11H2Z" fill="var(--cinema-film, #7895a0)" />
          <path d="M5 4H6V9H5Z M3 5H4V7H5V8H3Z M7 4H8V7H6V6H7Z M2 9H10V10H17V9H22V11H2Z" fill="var(--cinema-dark, #162431)" />
          <path d="M2 1H6V2H2Z M17 1H21V2H17Z M2 11H6V12H2Z M17 11H21V12H17Z" fill="var(--cinema-edge, #a3bdc9)" />
        </symbol>
      </defs>

      {/* A bevelled projector chassis, with a quiet opening for the label. */}
      <path className={styles.cinemaChassis} fill="var(--cinema-frame, #111b24)" d="M7 0H233V2H238V7H240V97H238V102H233V104H7V102H2V97H0V7H2V2H7Z" />
      <path d="M7 2H233V4H236V7H238V97H236V100H233V102H7V100H4V97H2V7H4V4H7Z M8 6V98H232V6Z" fill={`url(#${part("brass")})`} fillRule="evenodd" />
      <path d="M8 4H232V5H8Z M4 8H5V96H4Z" fill="var(--cinema-edge, #a3bdc9)" fillOpacity=".6" />
      <path d="M8 99H232V100H8Z M235 8H236V96H235Z" fill="var(--cinema-shadow, #080f16)" />
      <path d="M42 19H198V21H42Z M42 83H198V85H42Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M45 21H195V22H45Z M45 82H195V83H45Z" fill="var(--cinema-dark, #162431)" />
      <path d="M44 23H45V80H44Z M195 23H196V80H195Z" fill="var(--cinema-body, #304451)" fillOpacity=".65" />

      {/* The stock is clipped in its guides, so a short advance stays tidy. */}
      <path d="M43 4H197V19H43Z M41 86H199V101H41Z" fill="var(--cinema-shadow, #080f16)" />
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
      <path d="M9 43H12V72H17V84H39V87H14V76H9Z M228 43H231V76H226V87H201V84H223V72H228Z" fill="var(--cinema-shadow, #080f16)" />
      <path d="M12 47H14V71H18V82H38V84H16V74H12Z M226 47H228V74H224V84H202V82H222V71H226Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M16 46H34V49H37V73H34V76H21V73H18V63H16Z M206 46H224V63H222V73H219V76H206V73H203V49H206Z" fill="var(--cinema-body, #304451)" />
      <path d="M20 48H33V50H20Z M207 48H220V50H207Z M20 71H34V73H20Z M206 71H220V73H206Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M21 53H31V55H21Z M21 58H31V60H21Z M21 63H31V65H21Z M209 53H219V55H209Z M209 58H219V60H209Z M209 63H219V65H209Z" fill="var(--cinema-shadow, #080f16)" />
      <path d="M21 55H31V56H21Z M21 60H31V61H21Z M21 65H31V66H21Z M209 55H219V56H209Z M209 60H219V61H209Z M209 65H219V66H209Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M33 52H37V56H40V64H37V68H33Z M203 52H207V68H203V64H200V56H203Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M37 57H40V63H37Z M200 57H203V63H200Z" fill="var(--cinema-warm, #e1ae67)" />
      <path d="M35 53H37V55H35Z M203 53H205V55H203Z" fill="var(--cinema-shine, #e7f3f6)" />

      <g transform="translate(24 28)"><g className={styles.reelLeft}><use href={`#${part("reel")}`} x="-19" y="-19" width="38" height="38" /></g></g>
      <g transform="translate(216 28)"><g className={styles.reelRight}><use href={`#${part("reel")}`} x="-19" y="-19" width="38" height="38" /></g></g>

      {/* Leader marks, cooling fins, rivets and the little inspection plate. */}
      <path d="M8 6H18V8H8Z M222 6H232V8H222Z M7 83H10V96H7Z M230 83H233V96H230Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M8 86H9V88H8Z M8 91H9V93H8Z M231 86H232V88H231Z M231 91H232V93H231Z" fill="var(--cinema-edge, #a3bdc9)" />
      <path d="M15 91H35V98H15Z M205 91H225V98H205Z" fill="var(--cinema-dark, #162431)" />
      <path d="M17 93H19V96H17Z M21 93H23V96H21Z M25 93H27V96H25Z M29 93H33V94H29Z M29 95H32V96H29Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M207 93H222V94H207Z M207 95H215V96H207Z M218 95H222V96H218Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M39 7H41V14H39Z M199 7H201V14H199Z M38 90H40V96H38Z M200 90H202V96H200Z" fill="var(--cinema-edge, #a3bdc9)" />
      <use href={`#${part("screw")}`} x="6" y="72" width="5" height="5" />
      <use href={`#${part("screw")}`} x="229" y="72" width="5" height="5" />
      <use href={`#${part("screw")}`} x="34" y="78" width="5" height="5" />
      <use href={`#${part("screw")}`} x="201" y="78" width="5" height="5" />

      <g clipPath={`url(#${part("perimeter")})`}>
        <g className={styles.standbyBeam}>
          <path d="M38 57L102 18H120L39 61Z M38 61L133 86H112L37 64Z" fill={`url(#${part("beam-left")})`} />
          <path d="M202 57L138 18H120L201 61Z M202 61L107 86H128L203 64Z" fill={`url(#${part("beam-right")})`} />
        </g>
        <g className={styles.beamLeft} fill={`url(#${part("beam-left")})`}>
          <path d="M38 55L152 3H211L39 62Z" /><path d="M38 60L187 101H111L37 64Z" />
          <path d="M37 57L197 11V13L39 60Z M38 62L174 95V97L37 64Z" fillOpacity=".6" />
        </g>
        <g className={styles.beamRight} fill={`url(#${part("beam-right")})`}>
          <path d="M202 55L88 3H29L201 62Z" /><path d="M202 60L53 101H129L203 64Z" />
          <path d="M203 57L43 11V13L201 60Z M202 62L66 95V97L203 64Z" fillOpacity=".6" />
        </g>
        <g className={styles.lampLight}>
          <path d="M36 55H40V56H41V64H40V65H36V64H35V56H36Z M200 55H204V56H205V64H204V65H200V64H199V56H200Z" fill="var(--cinema-warm, #e1ae67)" />
          <path d="M37 56H40V64H37Z M200 56H203V64H200Z" fill="var(--cinema-hot, #fff2c4)" />
          <path d="M38 57H40V62H38Z M200 57H202V62H200Z" fill="var(--cinema-shine, #e7f3f6)" />
        </g>
        {/* Closed blades cover the aperture; the parent opens each group
            around its local origin without moving the projector housing. */}
        <g transform="translate(38 60)">
          <g className={styles.shutterLeft}>
            <path d="M-3-5H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-dark, #162431)" />
            <path d="M3 5H-3V2H-1V0H1V-2H3Z" fill="var(--cinema-body, #304451)" />
            <path d="M-3 1H-1V-1H1V-3H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-metal, #667f8f)" />
          </g>
        </g>
        <g transform="translate(202 60)">
          <g className={styles.shutterRight}>
            <path d="M-3-5H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-dark, #162431)" />
            <path d="M3 5H-3V2H-1V0H1V-2H3Z" fill="var(--cinema-body, #304451)" />
            <path d="M-3 1H-1V-1H1V-3H3V-2H1V0H-1V2H-3Z" fill="var(--cinema-metal, #667f8f)" />
          </g>
        </g>
        <path className={styles.powerTrim} d="M47 19H193V20H198V24H199V80H198V84H193V85H47V84H42V80H41V24H42V20H47Z"
          stroke="var(--cinema-power, #71d9ee)" strokeWidth="1" />
      </g>
    </svg>
  );
}

/** The arm's hinge is at (3, 9), independent of the body below it. */
export function CinemaSlate() {
  return (
    <svg className={styles.debugCrest} width="34" height="24" viewBox="0 0 34 24" fill="none" shapeRendering="crispEdges" aria-hidden="true">
      <path d="M2 9H33V22H31V24H4V23H2Z" fill="var(--cinema-shadow, #080f16)" />
      <path d="M3 10H32V22H3Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M4 12H31V21H4Z" fill="var(--cinema-shadow, #080f16)" />
      <path d="M4 10H31V12H4Z" fill="var(--cinema-edge, #a3bdc9)" />
      <path d="M5 10H9V12H5Z M14 10H18V12H14Z M23 10H27V12H23Z" fill="var(--cinema-dark, #162431)" />
      <path d="M6 14H14V15H6Z M17 14H29V15H17Z M6 18H29V19H6Z" fill="var(--cinema-metal, #667f8f)" />
      <path d="M6 16H8V17H6Z M10 16H13V17H10Z M17 16H22V17H17Z M24 16H28V17H24Z" fill="var(--cinema-edge, #a3bdc9)" />
      <path d="M15 13H16V20H15Z M4 21H31V22H4Z" fill="var(--cinema-body, #304451)" />
      <g className={styles.slateHinge}>
        <path d="M2 2H32V3H34V9H1V3H2Z" fill="var(--cinema-shadow, #080f16)" />
        <path d="M2 3H33V8H2Z" fill="var(--cinema-shine, #e7f3f6)" />
        <path d="M6 3H12L7 8H2V7Z M18 3H24L19 8H13Z M30 3H33V6L31 8H25Z" fill="var(--cinema-dark, #162431)" />
        <path d="M3 3H6V4H3Z M12 3H18V4H12Z M24 3H30V4H24Z" fill="var(--cinema-shine, #e7f3f6)" />
        <path d="M2 8H33V9H2Z" fill="var(--cinema-metal, #667f8f)" />
      </g>
      <path d="M1 8H5V11H1Z" fill="var(--cinema-metal, #667f8f)" /><path d="M2 9H4V10H2Z" fill="var(--cinema-dark, #162431)" />
      <path d="M31 20H32V21H31Z M3 20H4V21H3Z" fill="var(--cinema-edge, #a3bdc9)" />
    </svg>
  );
}
