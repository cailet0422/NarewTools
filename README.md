# NarewTools — 아일렛 온라인 모딩 툴킷

**한국어** · [English](README.en.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

아일렛 온라인(Islet Online)의 자체 엔진 **NarewEngine2**(나루엔진)를 분석해서 만든 Windows 10/11용 모딩 도구 모음입니다.
게임 파일을 직접 건드리지 않고 **모드**로 블록·가구·아이템·사운드를 추가하고, 텍스처·GUI·소리·모델·월드(맵)를 고친 뒤,
모드 매니저로 게임에 넣고 언제든 원래대로 되돌릴 수 있습니다. AI 에이전트(Claude Code 등)가 같은 기능을 직접 쓰게 하는 MCP 서버도 들어 있습니다.

**[⬇ 최신 버전 내려받기 (Releases)](https://github.com/cailet0422/NarewTools/releases/latest)**

![NarewStudio 월드 에디터에서 내 월드를 연 모습](docs/images/studio-world.png)
<sub>월드 에디터에서 연 내 월드. 이 성은 AI 에이전트가 NarewStudio 도구만으로 지었습니다.</sub>

> [!IMPORTANT]
> 비공식 팬 도구입니다. 게임 개발사·배급사와 관계가 없으며, 이 저장소와 릴리즈에는 **게임 파일이 들어 있지 않습니다**.
> 정식으로 설치한 게임이 있어야 쓸 수 있습니다. 온라인 기능이 있는 게임이므로 테스트는 싱글 플레이(내 월드)에서 하고, 게임 이용약관은 각자 확인하세요.
> 모드를 적용하기 전에는 원본이 자동으로 백업되지만, 사용에 따른 책임은 사용자에게 있습니다.

## 목차

- [구성](#구성)
- [기능](#기능)
- [요구 사항](#요구-사항)
- [설치](#설치)
- [처음 설정](#처음-설정)
- [빠른 시작: 첫 모드 만들기](#빠른-시작-첫-모드-만들기)
- [NarewStudio 사용법](#narewstudio-사용법)
- [NarewModManager 사용법](#narewmodmanager-사용법)
- [모드 형식](#모드-형식)
- [AI 에이전트 연결](#ai-에이전트-연결)
- [명령줄 도구 (narew)](#명령줄-도구-narew)
- [소스에서 실행 · 빌드 · 테스트](#소스에서-실행--빌드--테스트)
- [파일 위치](#파일-위치)
- [문제 해결](#문제-해결)
- [인게임 확인 상태와 알려진 한계](#인게임-확인-상태와-알려진-한계)
- [폴더 구성과 기술 문서](#폴더-구성과-기술-문서)
- [라이선스](#라이선스)

## 구성

| 프로그램 | 하는 일 |
|---|---|
| **NarewStudio.exe** | 모드 만들기. 게임 파일은 **읽기만** 하고, 바꾼 파일과 추가한 항목은 모드 프로젝트에 저장 |
| **NarewModManager.exe** | 만든/받은 모드를 켜고 끄고 순서를 정해 게임에 적용, 원본 복구 |
| **NarewAgent.exe** | AI 에이전트용 연결 프로그램(콘솔). MCP 서버와 명령줄로 NarewStudio의 기능을 도구 111개로 제공 |
| `narew` (Python) | 팩 풀기/만들기, 모델 ↔ glTF, 브릭(복셀) 파일 변환 등 명령줄 도구 (소스에서 실행) |

## 기능

**NarewStudio**

| 화면 | 기능 |
|---|---|
| 리소스 편집 | 게임 팩 20개 + `Language\` 의 모든 파일 탐색·검색. 이미지(원본/수정본 비교, 원래 형식·크기로 자동 변환, 색 조정), 사운드, 텍스트·XML(인코딩 보존, 문법 강조), 폰트, 헥스 보기. 파일을 끌어다 놓으면 교체 |
| 블록 텍스처 | `terrain.png` 블록 아틀라스 타일별 교체·색 조정, 빈 칸 표시 |
| 가구 텍스처 | 설치물 936종의 모델별 재질 텍스처 교체 + 3D 미리보기 |
| 새 리소스 추가 | **새 블록 / 새 가구 / 새 아이템 / 새 사운드** 도우미 (빈 번호 자동 계산, 아이콘 자동 생성, 제작 레시피) |
| GUI 편집 | 게임 화면 레이아웃 169개를 실제 그림으로 보며 옮기기·크기·글자·색 변경, 새 이미지·버튼·글자 추가 |
| 사운드 편집 | 파형 보기, 자르기·붙여넣기·페이드·볼륨·정규화·거꾸로·속도, 원래 형식 그대로 저장 |
| 월드 에디터 | 월드 맵(`.bwr`)·건축물 프리팹(`.iaf`) 3D 편집, 가구 배치, **내 월드(게임 저장본) 열기·캡처·게임에 다시 넣기** |
| 모델 뷰어 | 모델 3,633개·애니메이션 재생, 캐릭터 부위 조합, glTF(Blender)로 내보내기·가져오기 |
| 팩 탐색기 | `.pack` 파일 풀기·검증·만들기 |
| 내 모드 | 바뀐 것 목록·되돌리기, 모드 매니저에 설치, 배포용 .zip 만들기 |

**NarewModManager**: 모드 설치(zip 끌어다 놓기), 켜기/끄기, 우선순위, 충돌 표시, XML 표 **항목 단위 병합**(여러 모드가 같은 표에 항목을 더해도 모두 들어감), 게임에 적용 / 원본 복구, Steam 업데이트 감지.

**AI 에이전트**: "스폰 옆에 작은 집 지어 주고 침대 넣어 줘", "인벤토리 닫기 버튼을 왼쪽 아래로 옮겨 줘" 같은 요청을 에이전트가 직접 처리합니다. ASCII 지도와 그림으로 결과를 확인하고, 열려 있는 NarewStudio 창이 바뀐 내용을 바로 다시 읽습니다.

### 스크린샷

| 모델 뷰어 — 캐릭터 부위 조합·애니메이션 | GUI 편집 — 실제 게임 그림으로 화면 배치 |
|---|---|
| ![모델 뷰어](docs/images/studio-models.png) | ![GUI 편집](docs/images/studio-gui.png) |
| **사운드 편집 — 파형·구간 편집** | **새 리소스 추가 — 새 가구** |
| ![사운드 편집](docs/images/studio-sound.png) | ![새 가구 추가](docs/images/studio-create.png) |
| **가구 텍스처 — 재질별 텍스처·3D 미리보기** | **블록 텍스처 — 블록 아틀라스** |
| ![가구 텍스처](docs/images/studio-furniture.png) | ![블록 텍스처](docs/images/studio-blocks.png) |

## 요구 사항

- Windows 10 / 11 (64비트)
- 아일렛 온라인 설치본 (게임 폴더 = `IsletClient_release(x64).exe` 가 있는 폴더)
- 3D 화면(월드 에디터·모델 뷰어·가구 미리보기)에는 **OpenGL 3.3** 을 지원하는 그래픽 드라이버. 없어도 나머지 기능은 쓸 수 있습니다.
- 소스에서 실행할 때만: Python 3.10 이상 (3.12에서 확인)

## 설치

1. [Releases](https://github.com/cailet0422/NarewTools/releases/latest) 에서 `NarewTools-<버전>.zip` 을 내려받습니다.
2. 원하는 곳(예: `C:\Tools\`)에 압축을 풉니다. 설치 과정은 없습니다.
3. `NarewTools\NarewStudio.exe` 를 실행합니다.

```
NarewTools\
  NarewStudio.exe        모드 만들기
  NarewModManager.exe    모드 적용·복구
  NarewAgent.exe         AI 에이전트용 (콘솔)
  사용법.txt              간단 설명서
  _internal\             세 프로그램이 함께 쓰는 Qt/Python 런타임 — 지우거나 따로 옮기지 마세요
```

- 세 exe는 `_internal` 폴더를 같이 쓰므로 **`NarewTools` 폴더째** 옮기세요.
- 서명되지 않은 프로그램이라 Windows SmartScreen이 "PC 보호" 창을 띄울 수 있습니다. **추가 정보 → 실행** 을 누르면 됩니다.
- **자동 업데이트** (2.2.0부터): NarewStudio·모드 매니저를 켜면 새 버전을 확인하고(6시간에 한 번, 끌 수 있음), 바뀐 점을 보여 줍니다. **업데이트** 를 누르면 받아서 SHA-256으로 확인한 뒤, 프로그램을 닫을 때 새 파일로 바꾸고 다시 엽니다. 바꾸기 전 파일은 `%LOCALAPPDATA%\NarewTools\update\backup-<버전>\` 에 백업하고, 실패하면 원래대로 되돌립니다. 머리줄 오른쪽의 버전 버튼(`v2.2.0`)으로 바로 확인할 수도 있습니다. 폴더가 `Program Files` 처럼 쓸 수 없는 곳에 있으면 자동으로 바꿀 수 없으니 문서 등으로 옮기세요.
- 2.1.0 이하에서는 폴더를 통째로 교체하세요. 설정·모드 프로젝트·설치한 모드는 사용자 폴더에 따로 저장되어 그대로 남습니다([파일 위치](#파일-위치)).

## 처음 설정

### 1. 게임 폴더 고르기

NarewStudio와 NarewModManager를 처음 실행하면 게임 폴더를 묻습니다. `IsletClient_release(x64).exe` 가 있는 폴더를 고르세요.

- Steam 기본 위치: `C:\Program Files (x86)\Steam\steamapps\common\Islet Online`
- 다른 라이브러리에 설치했다면: `<라이브러리>\steamapps\common\Islet Online`
- 모르겠으면 Steam 라이브러리에서 게임을 오른쪽 클릭 → **관리 → 로컬 파일 보기** 로 열리는 폴더입니다.

두 프로그램과 AI 에이전트는 같은 설정을 함께 씁니다. 나중에 바꾸려면 각 프로그램의 게임 폴더 선택 메뉴에서 다시 고르면 됩니다.

### 2. 모드 프로젝트 만들기

NarewStudio 위쪽의 **새 모드** 를 눌러 모드 프로젝트를 만듭니다(모드 이름·만든 사람·버전·설명·저장 위치). 프로젝트는 기본으로 `문서\NarewStudio\Projects\` 아래에 저장되고,
이후의 모든 편집은 이 프로젝트에만 들어갑니다. 게임 폴더는 바뀌지 않습니다.

### 3. 확인

- 3D 화면이 검게 나오거나 안내 문구가 뜨면 그래픽 드라이버를 최신으로 업데이트하세요(OpenGL 3.3 필요).
- 오류가 나면 `%APPDATA%\NarewStudio\log.txt`, `%LOCALAPPDATA%\NarewModManager\log.txt` 를 확인하세요.

## 빠른 시작: 첫 모드 만들기

블록 하나를 새로 추가해서 게임에서 보는 예입니다.

1. **NarewStudio** → **새 모드** 로 프로젝트를 만듭니다.
2. 왼쪽 **새 리소스 추가** → **새 블록** 탭.
   - 이름·설명을 적고, 빈 블록 번호(자동으로 제안됨)와 성질을 복사할 비슷한 블록을 고릅니다.
   - 텍스처 이미지를 넣습니다(모든 면 같게, 또는 위·옆·아래 따로). 128×128로 자동 맞춥니다.
   - 필요하면 제작 레시피를 넣습니다(재료 칸에 아이템 이름이나 번호 일부를 입력하면 목록이 뜸).
   - 만들기를 누르면 아틀라스 텍스처·블록 표·블록 아이템·이름·아이콘·레시피가 모드에 들어갑니다.
3. 왼쪽 **내 모드** → **모드 매니저에 설치**.
4. **게임을 끈 상태에서** **NarewModManager** 를 열고, 모드를 체크한 뒤 **게임에 적용**.
5. 게임을 실행해 확인합니다.
6. 되돌리려면 게임을 끄고 NarewModManager에서 **원본 복구**.

## NarewStudio 사용법

### 리소스 편집

- 왼쪽 트리에서 팩·폴더를 탐색하거나 위쪽 검색창에 파일 이름 일부를 입력합니다.
- **이미지**: 원본과 수정본을 나란히 비교합니다. 새 그림을 끌어다 놓으면 원래 형식·크기로 자동 변환됩니다. 색조·채도·밝기·대비·색 입히기 조정.
- **텍스트·XML·셰이더**: 원래 인코딩(EUC-KR 등)·BOM·줄바꿈을 그대로 지키며 편집합니다. 문법 강조·검사·찾기.
- **사운드**: 재생, 같은 형식으로 교체. 세밀한 편집은 "사운드 편집기로 열기".
- **외부 편집기로 열기**: 포토샵·메모장 등에서 고친 뒤 적용할 수 있습니다.
- 바꾼 파일은 모드의 `files\` 에 들어갑니다. **내 모드** 화면에서 파일별로 되돌릴 수 있습니다.

### 블록 텍스처 · 가구 텍스처

- **블록 텍스처**: `terrain.png`(색상)·`terrain_specular.png`(반사광) 16×16 타일 아틀라스. 타일을 고르면 그 타일을 쓰는 블록이 표시되고, 이미지 교체(128×128 자동 맞춤)·색 조정·원본으로 되돌리기를 할 수 있습니다.
- **가구 텍스처**: 설치물 목록에서 가구를 고르면 모델의 재질별 텍스처와 3D 미리보기가 나옵니다. 게임이 실제로 읽는 텍스처만 연결되어 표시됩니다.

### 새 리소스 추가

| 탭 | 입력 | 모드에 들어가는 것 |
|---|---|---|
| 새 블록 | 이름·설명(한/영), 빈 블록 번호, 비슷한 블록(단단함·채집 도구·분류 복사), 텍스처(모든 면 / 위·옆·아래), 반사광(선택), 레시피(선택) | 아틀라스 텍스처, 블록 표 항목, 블록 아이템, 이름, 자동 아이콘, 레시피 |
| 새 가구 | 이름·설명, 빈 가구 번호, 아이템 번호, 기능 틀(앉기·눕기·조명 등 동작을 복사할 가구), 모델(**기존 가구 복사 + 텍스처 바꾸기** 또는 **glTF 가져오기**), 아이콘(이미지 또는 3D 미리보기 캡처), 레시피 | 모델 `.nwmt`(+텍스처), 설치물 표 항목, 아이템, 이름, 아이콘, 레시피 |
| 새 아이템 | 이름·설명, 아이템 번호, 틀 아이템(음식·물약·재료·도구 등 — 효과와 수치는 틀을 따름), 아이콘, 레시피 | 아이템 표 항목, 이름, 아이콘, 레시피 |
| 새 사운드 | 소리 파일(.wav/.ogg/.mp3), 이름, 배경음으로 쓸 날씨·시간과 볼륨, 또는 UI 효과음으로 쓸 곳 | 소리 파일, `sky.xml`·`Gui\sound.xml` 항목 |
| 만든 것 | — | 이 모드가 추가·수정한 항목 목록, 항목별로 빼기 |

엔진이 받아들이는 번호 범위(2026-10 게임 기준):

| 종류 | 쓸 수 있는 번호 | 비고 |
|---|---|---|
| 블록 | 149, 160–170, 192–239 | 171–191은 옛 프리팹이 쓰고 있어 피함 |
| 설치물(가구) | 58, 59, 597–599, 942–1023 | 엔진 한도 1–1023 |
| 아이템 | 19300–19999 권장 | 엔진 한도 1–19999. 블록 아이템은 블록 번호와 같음 |
| 제작 레시피 | 2599부터 아래로 | 엔진 한도 ≤ 2599 |

NarewStudio는 열 때마다 빈 번호를 다시 계산합니다. 게임 업데이트 뒤에는 모드를 다시 열어 공식 콘텐츠와 번호가 겹치지 않는지 확인하세요.

### GUI 편집

- 게임 화면 레이아웃(`gui.pack` 의 `Gui\*.xml`)을 실제 게임 그림으로 그려 보여 줍니다.
- 요소를 클릭해 끌거나 **방향키**(Shift = 10px)로 옮깁니다. **Ctrl+클릭** 이나 빈 곳을 끌어 여러 개를 함께 고를 수 있습니다.
- 위치·크기 숫자 입력, 글자·글자 크기·색 변경, **＋ 이미지 / 버튼 / 글자** 로 새 요소 추가(게임 그림 또는 내 PNG), **Delete** 로 지우기.
- 화면 크기 미리보기(1920×1080 / 1600×900 / 1280×720), 숨은 요소 보기, XML 직접 편집, 되돌리기.
- 버튼 그림은 "그림 편집" 으로 리소스 편집에 바로 연결됩니다. 바뀐 숫자만 고쳐 원본 형식을 유지합니다.

### 사운드 편집

- 게임 소리(WAV·OGG·MP3)의 파형을 보고 재생합니다. 드래그(또는 Shift+클릭)로 구간 선택, 휠로 확대.
- 잘라내기·복사·붙여넣기·선택만 남기기·무음·페이드 인/아웃·볼륨(dB)·정규화·거꾸로·속도, 되돌리기/다시.
- 다른 파일에서 가져오기(샘플레이트·채널 자동 맞춤), WAV/OGG/MP3로 내보내기.
- **모드에 저장** 은 원래 형식·샘플레이트·채널 그대로 저장합니다.

### 월드 에디터

게임 맵(`.bwr`)과 건축물 프리팹(`.iaf`)을 3D로 편집합니다.

| 조작 | 키 |
|---|---|
| 이동 | W A S D, 위/아래 Q E |
| 회전 / 이동 / 확대 | 왼쪽 드래그 / 가운데 드래그 / 휠 |
| 되돌리기 / 다시 / 저장 | Ctrl+Z / Ctrl+Y / Ctrl+S |
| 설치물 90° 돌리기 | R |
| 놓인 설치물 돌리기 / 조명 켜기·끄기 / 지우기 ("✋ 설치물 편집") | R / T / Delete |

- 도구: 보기·놓기·지우기·칠하기·스포이드·상자 채우기/비우기·프리팹 붙이기·**이미지 도트**·**설치물 놓기**·**설치물 편집**·시작 위치·오브젝트 삭제.
- **설치물 놓기**: 오른쪽 "설치물" 탭에서 가구를 고르고 클릭합니다. 초록 = 놓을 수 있음, 빨강 = 막힘. 가구 밑 충돌 칸은 자동으로 채워집니다. 조명은 켜진 채로 놓입니다.
- **설치물 편집**: 이미 놓인 가구를 클릭해 고르면(하늘색 상자) **R** 로 놓인 칸을 중심으로 90° 돌리고(충돌 칸도 함께, 보관함 속 아이템은 그대로), **T** 로 조명을 켜고 끄고, **Delete** 로 지웁니다. 조명 표시는 켜짐 = 노랑, 꺼짐 = 회색입니다. 꺼진 조명은 게임에서 빛나지 않습니다.
- **이미지 도트** ("🖼 이미지 고르기…"): 그림 파일(또는 클립보드 그림)을 고르고 크기·방향(벽 4방향 또는 바닥)·양면·색 진하게·디더링·쓸 블록을 정한 뒤 미리보기를 봅니다. 맵을 클릭하면 그 칸에 그림의 아래 가운데가 섭니다(벽은 앞면, 바닥은 가운데). 픽셀마다 색이 가장 가까운 블록을 쓰고, 무늬가 심한 블록은 기본으로 빼며, 투명한 픽셀은 그대로 둡니다. 양면 벽은 뒤에서 봐도 글자가 바로 읽힙니다. 되돌리기 한 번에 지워집니다.
- 큰 맵은 카메라 주변만 불러옵니다(보는 거리 조절). 미니맵을 눌러 이동합니다.

**내 월드 편집하기** (싱글 플레이 월드)

1. **💾 내 월드** 에서 게임 저장본(`<게임 폴더>\Local\<월드>.bwr`)을 엽니다. 게임이 저장한 상태가 열립니다.
2. 고친 뒤 **저장** → 원본은 그대로 두고 `문서\NarewStudio\Worlds\` 에 새 파일로 저장됩니다.
3. 그 파일을 연 상태에서 **게임을 끄고** **🎮 내 월드에 적용** → 게임의 저장본을 이 파일로 바꿉니다.
   - 바꾸기 전에 현재 저장본(`.bwr .lnd .npc .db`)을 `문서\NarewStudio\WorldBackups\<월드>_<시각>\` 에 백업합니다.
   - **↩ 백업으로** 를 누르면 언제든 되돌립니다.
   - 게임이 켜져 있으면 거부합니다. 고친 파일보다 게임이 나중에 저장했다면 경고합니다.

**월드 캡처** — 지금 하는 월드를 `.bwr` 로 보관

- **📸 현재 월드 저장** 또는 **캡처 창**(항상 위에 뜨는 작은 창). 게임 중에도 **Ctrl+Alt+S** 로 저장됩니다.
- "게임이 저장할 때마다 자동으로 남기기" 를 켜면 게임이 저장할 때마다 한 벌씩 남습니다.
- 게임하면서 캡처 창만 띄우려면 `NarewStudio.exe --capture`.
- 저장되는 것은 **게임이 마지막으로 저장한 상태**입니다(게임 안에 코드를 넣지 않음). 내 PC에 저장되는 월드만 캡처할 수 있습니다.

### 모델 뷰어 · Blender

- 모델을 종류별로 탐색하고 애니메이션을 재생합니다. 캐릭터는 옷·머리·얼굴·신발 부위를 더블클릭해 한 장면에 조합합니다.
- **glTF로 내보내기** → Blender에서 수정 → **glTF에서 가져오기** 로 모델·애니메이션을 바꿉니다. 새 애니메이션 추가, 재생 속도 변경도 됩니다.
- Blender 팁: 가져오기 전에 장면 프레임 레이트를 **30 fps** 로, 내보낼 때 **Include → Custom Properties** 를 켜세요(원본 정보가 보존됨).

### 팩 탐색기 · 내 모드

- **팩 탐색기**: 팩 목록·파일 상세(압축·MD5), 팩/폴더/전체 추출(MD5 검증), 폴더로 팩 만들기.
- **내 모드**: 모드 정보·미리보기 그림, 바뀐 파일과 표 병합 목록(항목별 되돌리기), **모드 매니저에 설치**, **배포용 .zip 만들기**.

## NarewModManager 사용법

1. 게임 폴더를 고릅니다(NarewStudio와 같은 설정).
2. 모드를 설치합니다: 모드 `.zip` 을 창에 끌어다 놓기 / **＋ .zip 설치…** / **폴더에서 설치…** / NarewStudio의 **모드 매니저에 설치**.
3. 체크박스로 켜고, 끌거나 ▲▼로 순서를 정합니다(**위쪽이 우선**). 오른쪽에서 모드의 파일·표 병합과 다른 모드에 가려지는 파일을 볼 수 있습니다.
4. **게임을 끈 상태에서** **게임에 적용**. 되돌리려면 **원본 복구**.

![NarewModManager — 설치한 모드 목록과 우선순위](docs/images/modmanager.png)

적용 방식:

| 방식 | 동작 |
|---|---|
| 팩 패치 (기본, 권장) | 처음 한 번 원본 팩을 `data\<팩>.pack.orig` 로 보관하고, 적용할 때마다 원본 + 켜진 모드로 팩을 새로 만듭니다. 안 바뀐 파일은 재압축 없이 복사 |
| 낱개 파일 (실험적) | 모드 파일을 게임 폴더에 낱개로 둡니다. 팩은 건드리지 않음. GUI 이미지만 확인됨 |

- **표 병합**: 켜진 모드의 `merge\` 패치를 우선순위 낮은 것부터 차례로 적용합니다. 같은 항목을 두 모드가 고치면 위쪽 모드가 이깁니다. 원본 인코딩·줄바꿈은 유지됩니다.
- 게임 폴더의 낱개 데이터 파일(`Language\string.xml` 등)은 처음 한 번 `<파일>.orig` 로 백업하고 바꿉니다. 실행 파일·DLL은 절대 건드리지 않습니다.
- **Steam 업데이트** 나 "게임 파일 무결성 검사" 로 파일이 바뀌면 다음 실행 때 감지해서 알려 줍니다. 그때 **게임에 적용** 을 다시 누르세요.

## 모드 형식

모드는 아래 구조의 폴더(배포할 때는 이 폴더를 zip으로 묶은 파일)입니다.

```
내모드/
  mod.json        {"id": "pastel-blocks", "name": "파스텔 블록", "version": "1.0.0", "author": "", "description": "", "format": 2}
  preview.png     (선택) 목록에 보일 그림
  files/          통째로 바꾸거나 새로 넣는 파일 — 게임 팩 안의 경로 그대로 (대소문자 무시)
    terrain.png
    Item/Icon/brick/icon_brick_149.png
  merge/          게임 표(XML)에 항목 단위로 더하거나 바꾸는 패치
    XML/item_table.xml
    Language/string.xml
```

`merge\` 패치 예 (UTF-8). 같은 키의 항목이 있으면 통째로 바꾸고, 없으면 표 끝에 더합니다.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<merge>
  <brick id="149"> … 항목 전체 … </brick>
  <item id="149"> … </item>
</merge>
```

- `files\` 는 파일 전체를 바꿉니다. 같은 파일을 두 모드가 바꾸면 우선순위가 높은 쪽만 들어갑니다.
- `merge\` 는 항목 단위라서 여러 모드가 같은 표에 각자 항목을 더해도 모두 들어갑니다.
- **원본 게임 파일을 통째로 배포하지 마세요.** 바꾸거나 새로 만든 것만 담으세요(NarewStudio의 "배포용 .zip 만들기" 가 그렇게 만듭니다).
- 자세한 규칙: [docs/07-new-content.md](docs/07-new-content.md)

## AI 에이전트 연결

NarewStudio의 모든 기능이 **도구 111개**(맵·건물·가구 배치, 새 블록·가구·아이템·사운드, 모든 게임 파일, XML 표, GUI, 사운드, 모델, 팩, 모드 매니저)로 제공됩니다.
에이전트는 ASCII 지도와 그림(위에서 본 지도·3D·GUI·모델·파형)으로 결과를 확인합니다.

| 3D 그림 (`world_render` view=iso) | 위에서 본 평면도 (view=top) |
|---|---|
| ![에이전트가 보는 3D 그림](docs/images/agent-castle-3d.png) | ![에이전트가 보는 평면도](docs/images/agent-castle-top.png) |

<sub>에이전트가 맨 위 화면의 성을 지으면서 결과를 확인할 때 보는 그림입니다. 주황색 상자 = 가구.</sub>

### Claude Code

**소스(이 저장소)로 쓰기** — 가장 간단합니다.

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
pip install -r NarewTools/requirements.txt
```

`NarewTools` 폴더에서 Claude Code를 실행하면 `.mcp.json` 의 **narew-studio** 서버가 잡힙니다(처음 한 번 승인). 에이전트용 안내는 `AGENTS.md`(`CLAUDE.md` 가 불러옴)에 있습니다.

**릴리즈 exe로 쓰기** — Python 없이:

```bash
claude mcp add narew-studio -- "C:\Tools\NarewTools\NarewAgent.exe" mcp
```

### 다른 MCP 클라이언트 (Claude Desktop 등)

stdio 서버로 `NarewAgent.exe mcp` 를 등록합니다. 예: Claude Desktop의 `claude_desktop_config.json`

```json
{
  "mcpServers": {
    "narew-studio": {
      "command": "C:\\Tools\\NarewTools\\NarewAgent.exe",
      "args": ["mcp"]
    }
  }
}
```

소스로 실행할 때는 `python -m studio.agent mcp` (작업 폴더 = `NarewTools`).

### 명령줄만 쓰는 에이전트

```bash
NarewAgent.exe tools
```
```bash
NarewAgent.exe call status --pretty
```
```bash
NarewAgent.exe guide world
```

- `call <도구> 키=값 …` 한 번 호출, `run steps.json` 여러 단계(월드 세션 유지), `serve` JSON 한 줄씩 주고받기.
- 소스에서는 `NarewAgent.exe` 대신 `python -m studio.agent`.
- 사용법 주제: `overview`, `world`, `content`, `gui`, `sound`, `models`, `mod`, `safety`.

### NarewStudio 창과 함께 쓰기 · 안전장치

- 에이전트와 창은 같은 설정(게임 폴더·모드 프로젝트)을 씁니다. 에이전트가 저장하면 창이 1초 안에 다시 읽습니다(창에 저장 안 한 편집이 있으면 덮어쓰지 않음).
- 에이전트는 창이 지금 보여 주는 월드·카메라 위치를 알 수 있어서 "여기에 지어 줘" 가 통합니다.
- 게임 폴더는 읽기만 합니다. 게임을 실제로 바꾸는 도구(`world_apply_to_game`, `world_restore_backup`, `manager_apply`, `manager_restore`)는 `confirm=true` 없이 부르면 무엇이 바뀌는지만 알려 주고, 에이전트는 **사용자에게 물어본 뒤에만** 실행합니다. 게임이 켜져 있으면 거부하고, 바꾸기 전에 백업합니다.
- 자세한 내용: [docs/09-ai-agents.md](docs/09-ai-agents.md)

## 명령줄 도구 (narew)

외부 패키지 없이 Python 3.10+ 만으로 동작합니다(텍스처를 `.glb` 에 넣을 때만 Pillow 선택). 저장소 폴더에서 실행합니다.

```bash
python -m narew --help
```

**팩 파일**

```bash
python -m narew info "<게임 폴더>\data"
```
```bash
python -m narew list "<게임 폴더>\data\gui_skin.pack" -f "*login*"
```
```bash
python -m narew extract "<게임 폴더>\data" -o D:\extracted --verify
```
```bash
python -m narew verify "<게임 폴더>\data\item.pack"
```
```bash
python -m narew build D:\extracted\xml -o D:\out\xml.pack
```
```bash
python -m narew patch D:\work\gui_skin.pack D:\mymod
```

- `extract` 는 `<출력>\<팩이름>\` 폴더와 `<팩이름>.narew.json`(원래 순서·속성)을 만듭니다. `build` 는 이 정보를 써서 수정하지 않았다면 원본과 바이트 단위로 같은 팩을 만듭니다.
- `patch` 는 오버레이 폴더의 파일로 팩 안의 파일을 교체/추가합니다(제자리 패치 시 처음 한 번 `.orig` 백업). 게임에 넣을 때는 모드 매니저를 쓰는 것을 권장합니다.

**모델 ↔ glTF**

```bash
python -m narew model2gltf D:\extracted\effect\Effect\brick_effect.nwmt -o brick.glb
```
```bash
python -m narew gltf2model D:\work\f1_edit.glb -o D:\work\f1_mod
```
```bash
python -m narew gltf2anim D:\work\f1_edit.glb -s f1.nwbt -t f1_combat_attack_onewp01.nwat -o out.nwat
```

**브릭(복셀) 파일** — `.iaf` 프리팹, `.bwr` 월드

```bash
python -m narew brick info D:\extracted\map\map\64_0.bwr
```
```bash
python -m narew brick export D:\extracted\architecture\Architecture\building\Choga_big_A01.iaf -o choga.vox
```
```bash
python -m narew brick import mybuilding.vox -o mybuilding.iaf
```
```bash
python -m narew brick paste D:\work\64_0.bwr mybuilding.iaf --at 24,110,24 -o D:\work\64_0_mod.bwr
```

- `export` 는 확장자로 형식을 고릅니다: `.vox`(MagicaVoxel), `.png`(`--view top|front|side`), `.json`.
- 자세한 내용: [docs/04-model-formats.md](docs/04-model-formats.md), [docs/05-brick-formats.md](docs/05-brick-formats.md)

## 소스에서 실행 · 빌드 · 테스트

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
cd NarewTools
```
```bash
pip install -r requirements.txt
```
```bash
python -m studio
```
```bash
python -m modmanager
```

**exe 빌드** (PyInstaller 필요)

```bash
pip install pyinstaller
```
```bash
python tools\build_exe.py
```

`dist\NarewTools\` 에 세 exe가 만들어지고, 각 exe의 자체 점검(`--selftest`, 에이전트는 MCP 인사)을 통과하면 `dist\NarewTools-<버전>.zip` 과 `.zip.sha256` 을 만듭니다. 자동 업데이트는 GitHub 릴리스(태그 `v<버전>`)에 **이 두 파일이 함께 올라가 있어야** 새 버전을 받습니다. 빌드 점검에는 만든 NarewAgent로 가짜 폴더에 업데이트를 넣어 보는 것도 들어 있습니다.
`dist\NarewTools` 를 쓰는 중이면 `--dist <다른 폴더>` 로 다른 곳에 빌드할 수 있습니다.

**테스트** — 실제 게임 폴더는 읽기만 하고, 적용 테스트는 팩 일부를 복사한 가짜 게임 폴더에서 합니다.

```bash
python tests\test_agent.py "<게임 폴더>" D:\scratch\t_agent
```
```bash
python tests\test_agent_live.py "<게임 폴더>" D:\scratch\t_live
```

나머지 테스트(`tests\test_*.py`, `smoke_*.py`)의 인자는 각 파일 맨 위 설명을 참고하세요.

## 파일 위치

| 무엇 | 위치 |
|---|---|
| NarewStudio 설정·로그 | `%APPDATA%\NarewStudio\` (`log.txt`) |
| 모드 프로젝트 | `문서\NarewStudio\Projects\` (새 모드를 만들 때 바꿀 수 있음) |
| 저장/캡처한 월드 | `문서\NarewStudio\Worlds\` |
| 내 월드 적용 전 백업 | `문서\NarewStudio\WorldBackups\` |
| 모드 매니저 (설치한 모드·설정·로그) | `%LOCALAPPDATA%\NarewModManager\` (`mods\`, `settings.json`, `deployed.json`, `log.txt`) |
| 모드 적용 시 원본 백업 | `<게임 폴더>\data\<팩>.pack.orig`, `<게임 폴더>\<파일>.orig` |
| AI 에이전트 ↔ 창 연결 | `%APPDATA%\NarewStudio\agent\` (네트워크·포트를 쓰지 않음) |
| 자동 업데이트 (설정 · 받은 파일·백업·기록) | `%APPDATA%\NarewTools\update.json` · `%LOCALAPPDATA%\NarewTools\update\` |

## 문제 해결

| 증상 | 해결 |
|---|---|
| "Windows의 PC 보호" 창 | 서명되지 않은 프로그램이라서 그렇습니다. **추가 정보 → 실행** |
| 3D 화면이 검거나 안내 문구만 보임 | 그래픽 드라이버를 최신으로 (OpenGL 3.3 필요). 노트북은 고성능 GPU로 실행 |
| 게임에 적용이 안 됨 | 게임을 완전히 끄고 다시 시도. 오류 내용은 `%LOCALAPPDATA%\NarewModManager\log.txt` |
| Steam 업데이트 뒤 모드가 사라짐 | 업데이트가 팩을 덮어써서 그렇습니다. 모드 매니저에서 **게임에 적용** 을 다시 누르세요 |
| 게임이 이상해짐 / 완전히 원래대로 | 모드 매니저 **원본 복구**. 그래도 안 되면 Steam → 속성 → 설치된 파일 → **게임 파일 무결성 검사** |
| 내 월드를 되돌리고 싶음 | 월드 에디터 **↩ 백업으로**, 또는 `문서\NarewStudio\WorldBackups\` 의 파일 |
| AI 에이전트 도구가 안 보임 | Claude Code는 `/mcp` 에서 narew-studio 승인 여부 확인. 소스 실행이면 `pip install -r requirements.txt` 확인 |

## 인게임 확인 상태와 알려진 한계

| 기능 | 상태 |
|---|---|
| 새 블록·새 가구·손 제작 레시피·이름 문자열 | ✅ 게임에서 확인 |
| UI 효과음 교체, GUI 그림·배치 변경 | ✅ 게임에서 확인 |
| 월드 편집(블록·가구) + 내 월드에 적용 | ✅ 게임에서 확인 |
| 수정한 모델·애니메이션의 게임 내 로드 | ⚠ 아직 미확인 |
| "새 아이템" 일반 아이템의 효과, 날씨·시간 배경음 | ⚠ 아직 미확인 |
| 멀티 플레이 | ⚠ 아직 미확인 |

- 월드 에디터의 설치물 놓기는 표지판·사진·텃밭·상점·우편함 등 자체 데이터가 필요한 80종을 아직 지원하지 않습니다.
- 프로그램 화면은 현재 한국어만 지원합니다.
- 미확인 기능에서 문제가 생기면 **원본 복구** 로 되돌리세요.

## 폴더 구성과 기술 문서

```
narew/       포맷 라이브러리 + 명령줄 도구 (pack, brick, model ↔ glTF, 모드 형식, 표 병합)
studio/      NarewStudio — core, svc_*, creator(새 리소스), worldedit(화면 없는 월드 편집), gl/(OpenGL), ui/(Qt), agent/(AI 에이전트)
modmanager/  NarewModManager — core(적용 엔진), ui
tests/       테스트 (가짜 게임 폴더 사용)
tools/       build_exe.py(exe 빌드), PE/RTTI 분석·모델 검증 스크립트
docs/        기술 문서 (한국어)
AGENTS.md    AI 에이전트용 안내, .mcp.json = Claude Code용 MCP 서버 등록
```

| 문서 | 내용 |
|---|---|
| [01 팩 포맷](docs/01-pack-format.md) | `.pack` 구조 (20개 팩 전부 바이트 동일 재빌드) |
| [02 엔진 구조](docs/02-engine-architecture.md) | NarewEngine2 계층·클래스 개요 |
| [03 로드맵](docs/03-roadmap.md) | 앞으로 할 일 |
| [04 모델 포맷](docs/04-model-formats.md) | `.nwmt/.nwbt/.nwat` ↔ glTF |
| [05 브릭 포맷](docs/05-brick-formats.md) | `.iaf/.bwr` 복셀 월드 |
| [06 프로그램 설명서](docs/06-studio-modmanager.md) | NarewStudio·NarewModManager 상세 |
| [07 새 콘텐츠](docs/07-new-content.md) | 엔진 한도, 표 병합 규칙 |
| [08 GUI 레이아웃](docs/08-gui-layout.md) | `Gui\*.xml` 편집 |
| [09 AI 에이전트](docs/09-ai-agents.md) | 도구 111개, 연결, 안전장치 |

문서에 나오는 `re/` 분석 산출물(게임 바이너리 덤프·디컴파일 결과)은 저장소에 포함하지 않았습니다.

## 라이선스

[MIT](LICENSE). 아일렛 온라인과 관련 상표·리소스의 권리는 각 권리자에게 있습니다.
