/** 화면 디자인 토큰(2026-10-18 전면 개편, 시안 `.omc/plan/2026-10-18-service-ui-records.design.html`).
 *
 * 기존 화면이 Tailwind 기본 색 이름(slate·indigo·sky·amber·red·emerald)을 쓰므로, 이름은 그대로 두고 값을 이 제품의
 * 팔레트로 바꿔 모든 화면이 한 번에 같은 인상을 갖게 한다:
 *   slate  → 녹회색 중립(바탕 #F0F2EE · 선 #D8DDD6 · 흐린 글 #5B6762) + 900은 잉크(진한 소나무색 #15372F, 주 버튼·제목)
 *   indigo·sky → 잉크 계열 옅은 색(안내 띠·선택 상태)
 *   amber → 주의(#8A5A0B on #FBF1DC) · red → 오류(#A61B1B on #FCEBEA) · emerald → 성공(차분한 초록)
 *   seal(인주 #C8402A)은 "검증" 도장과 로고에만. ramp(특급→초급, 투입률 막대)는 g1~g4.
 */
const slate = {
  50: "#F0F2EE", 100: "#E6EAE5", 200: "#D8DDD6", 300: "#C3CBC4", 400: "#8A948F",
  500: "#5B6762", 600: "#46524D", 700: "#333E3A", 800: "#24504A", 900: "#15372F", 950: "#0E2620",
};
const pine = {
  50: "#EEF4F1", 100: "#E3ECE7", 200: "#C9DBD2", 300: "#A9C6B8", 400: "#79A898",
  500: "#3D7465", 600: "#2C5A4F", 700: "#24504A", 800: "#1F433A", 900: "#15372F", 950: "#0E2620",
};

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        slate,
        indigo: pine,
        sky: pine,
        amber: {
          50: "#FBF1DC", 100: "#F7E6C2", 200: "#EED39A", 300: "#E2BC6A", 400: "#C99A3A",
          500: "#B07E22", 600: "#8A5A0B", 700: "#744B09", 800: "#5E3D08", 900: "#4A3006",
        },
        red: {
          50: "#FCEBEA", 100: "#F8D9D6", 200: "#F0B6B0", 300: "#E48C83", 400: "#D2615A",
          500: "#BE3A33", 600: "#A61B1B", 700: "#8A1616", 800: "#701212", 900: "#5A0F0F",
        },
        emerald: {
          50: "#EAF4EE", 100: "#D6EADD", 200: "#B2D6BF", 300: "#86BD9C", 400: "#5AA27B",
          500: "#3A8761", 600: "#2E6E4F", 700: "#255A41", 800: "#1D4734", 900: "#163628",
        },
        ink: pine[900],
        canvas: slate[50],
        paper: "#FFFFFF",
        line: slate[200],
        seal: { DEFAULT: "#C8402A", soft: "#FFFCFA" },
        g1: "#15372F", g2: "#3D7465", g3: "#79A898", g4: "#B9D5CA", g0: "#E7ECE8",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans KR"', "system-ui", "-apple-system", '"Apple SD Gothic Neo"', "sans-serif"],
      },
      fontSize: {
        // 본문 14px(데이터가 빽빽한 업무 화면) · 제목 22 · 편성안 글자 48
        base: ["14px", "1.55"],
        plan: ["48px", { lineHeight: "1", letterSpacing: "-0.03em", fontWeight: "700" }],
      },
    },
  },
  plugins: [],
};
