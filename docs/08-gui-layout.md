# GUI 레이아웃 (`gui.pack`의 `Gui\*.xml`, `gui_skin.pack`)

게임의 창·버튼은 두 팩으로 이루어진다.

| 팩 | 내용 |
|---|---|
| `gui.pack` | `Gui\*.xml` 169개 — 화면(창)마다 하나. 요소의 위치·크기·그림·글자 (`Gui\sound.xml`은 UI 효과음 표) |
| `gui_skin.pack` | `gui_skin\<폴더>\*.png` 1,306개 — 버튼·창 테두리·아이콘 그림. `00_Common`(공통 버튼), `01_Popup_Frm`(창 틀), `02_Mainmenu` … `19_Login_Create` 등 |

## 1. 레이아웃 파일

요소 하나가 `<node type="…">`이고, 바로 아래 자식 요소가 그 요소를 설명한다(2026-10-13, 169개 파일·노드 6,838개 조사).

| 자식 | 뜻 |
|---|---|
| `<pos x y [center]>` | 부모 안에서 `align` 기준점으로부터의 이동 |
| `<align x="left\|center\|right" y="top\|center\|bottom">` | 기준점 (없으면 left/top). 예: right/top + `pos x=-13 y=20` → 오른쪽 위 모서리에서 왼쪽 13, 아래 20 |
| `<size width height [lock_parent]>` | 크기. 없으면 그림 크기, 그것도 없으면 부모 크기 |
| `<sizeoption width="parent_width" height="parent_height\|normal">` | 부모 크기로 늘리기 |
| `<texture [uv="wrap"] [padding]>폴더\파일.png</texture>` | `gui_skin\` 아래 그림 (`uv="wrap"`은 바둑판 반복). 일부는 다른 팩의 그림을 파일 이름만으로 가리킨다 |
| `<skinset width height left_x right_x top_y bottom_y [folding_x]>` | 그림을 9분할로 늘리는 창 틀 (가장자리 두께) |
| `<text alignx aligny>글자</text>`, `<font size>파일.ttf</font>`, `<color font="0xAARRGGBB" outline>` | 글자 요소 |
| `<event type="mouse_click" id>` | 코드가 쓰는 이벤트 번호 — 바꾸지 않는다 |

- 노드 종류: image 1,827, extended_text 832, button 669, dialog 434, unfold 254, gauge 92, dragview 57 …
- 창의 맨 위 노드는 대개 `visible="0"`이다(게임이 열 때 보인다).
- 파일 인코딩이 섞여 있다(UTF-8 53, EUC-KR 69, 선언 없음 47, BOM 있는 것 포함). 엄격한 XML 파서로는 35개가 안 읽힌다.

## 2. 고치는 법

- **그림**: 리소스 편집에서 `gui_skin\…png`을 교체(같은 크기 권장 — 9분할 창 틀은 `skinset` 값과 맞아야 함).
- **배치**: NarewStudio **🖼 GUI 편집** — 화면 목록에서 고르면 실제 그림으로 그려진다. 요소를 클릭해 끌거나 방향키(Shift: 10px)로 옮기고,
  오른쪽에서 위치·크기를 숫자로 고치거나 XML을 직접 고친 뒤 "텍스트 적용". "모드에 저장"은 파일 전체를 모드에 넣는다.
- **여러 개**: Ctrl+클릭 또는 빈 곳을 끌어 상자로 고른 뒤 함께 끌기/방향키. 고른 요소 안에 든 요소는 따라 움직인다.
- **새 요소**: ＋ 이미지·＋ 버튼(그림 고르기 — 게임 그림 또는 "내 그림 파일 가져오기"로 모드의 `gui_skin\mod\`에 넣은 PNG)·＋ 글자.
  고른 요소를 품은 창(dialog/unfold/dragview 또는 자식이 있는 image) 안에 `desc="NarewStudio"`로 들어간다.
  새 버튼은 모양만 있다 — 게임 코드가 모르는 버튼이라 눌러도 동작은 없다.
- **글자**: 글자·크기·색을 바꾸면 `<text>`, `<font size>`, `<color font>`만 고친다(파일 인코딩에 없는 글자는 `&#NNNN;`).
- **지우기**: Delete. id나 이벤트가 있는 요소는 게임 코드가 쓰므로 경고한다(숨기려면 크기를 0으로).
  편집기는 파일을 다시 쓰지 않고 바뀐 숫자만 고친다(인코딩·줄바꿈·주석 그대로 — `studio/guilayout.py`).
- `id`, `type`, `event`는 게임 코드가 찾아 쓰므로 바꾸거나 지우지 않는다.

## 3. 확인

- 그림 교체: 2026-10-13 인게임 확인 (로고, 닫기 버튼).
- **배치 변경: 2026-10-13 인게임 확인** — 테스트 모드 "나루 테스트 UI"(모든 닫기 버튼 파란색 + 인벤토리 닫기 버튼 `pos -13,20 → -133,60`)를
  모드 매니저로 적용, 게임에서 버튼 색과 위치가 바뀐 것을 확인. 즉 `Gui\*.xml`을 팩 패치로 바꾸면 게임이 그 배치를 쓴다.
