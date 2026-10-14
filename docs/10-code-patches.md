# 10. 엔진 코드 패치 (모드 형식 3, `patches/`)

데이터 파일이 아니라 **엔진 코드 안에 상수로 박혀 있는 값**(예: 시야거리 단계별 거리)은 `files/`나
`merge/`로 바꿀 수 없습니다. 그런 경우를 위해 모드에 `patches/<이름>.json`을 넣을 수 있습니다.
모듈은 `narew/codepatch.py`, 적용은 NarewModManager가 합니다.

## 형식

```json
{
  "format": 1,
  "description": "시야거리 슬라이더 (20~225칸, 기본 190칸)",
  "targets": [
    {
      "file": "IsletEngine_release(x64).dll",
      "sha256": "8e4c0cd9…",
      "patches": [
        {"rva": "0x12c7d0", "original": "8b91d81e…", "patched": "8b81d81e…", "note": "…"},
        {"symbol": "?UpdateViewDistance@COption@Islet@@QEAAXXZ", "offset": "0x0", "original": "…", "patched": "…"}
      ]
    }
  ]
}
```

- 위치는 `rva` 또는 내보내기 심볼(`symbol` + `offset`). 엔진 DLL은 C++ 심볼을 그대로 내보냅니다.
- `original`은 손대지 않은 파일의 바이트와 정확히 같아야 하고, `patched`는 같은 길이입니다.
- `sha256`이 있으면 파일 전체가 그 버전일 때만 적용합니다. 패치끼리 서로 점프하거나(코드 동굴),
  절대 주소를 계산해 넣었다면 반드시 넣으세요 — 게임이 업데이트되면 그냥 건너뜁니다.

## 안전 규칙 (`codepatch.plan`)

1. 대상은 엔진 DLL 네 개뿐: `IsletEngine_release(x64).dll`, `IsletEngine_release.dll`,
   `NarewEngine2_Release64.dll`, `NarewEngine2_Release.dll`. 클라이언트 exe·Steam·서드파티 DLL은 거부.
2. 로더가 다시 쓰는 바이트(베이스 재배치, `.reloc`)와 겹치면 거부 — 32비트 DLL에서 특히 중요합니다.
3. 파일 안(헤더 또는 섹션의 파일 데이터)이어야 하고, 같은 대상 안의 패치끼리 겹치면 거부.
4. 한 패치 파일은 **대상 전체가 전부 아니면 전무**: 어느 대상의 패치 하나라도 안 맞으면 그 파일의 어느
   DLL에도 쓰지 않습니다(한 DLL에 넣은 코드가 다른 DLL의 코드·데이터를 쓰는 경우 — 예: 프리캠).

## 모드 매니저

- 팩과 같은 방식: 처음 한 번 `<dll>.orig`로 백업하고, 적용할 때마다 백업에서 다시 만듭니다.
- 켜진 모드의 패치를 **우선순위가 높은 것부터** 넣고, 위 모드가 이미 바꾼 바이트를 건드리는 패치,
  버전이 다른 패치는 건너뛰고 보고합니다(“건너뜀”).
- 더 이상 패치가 없는 DLL은 백업으로 되돌립니다. “원본 복구”는 DLL도 되돌립니다.
- Steam 업데이트/파일 검사로 DLL이 바뀌면 그 파일을 새 원본으로 보고 옛 백업을 버립니다.
- `patches/`가 있는 모드는 `mod.json`의 `format`이 3이 됩니다(2.4.1 이하 도구는 설치를 거부 →
  패치를 조용히 빠뜨리지 않음). 패치가 없는 모드는 계속 형식 2로 저장됩니다.
- 테스트: `python tests/test_codepatch.py <게임 폴더> <임시 폴더>` (가짜 게임 폴더, 실제 설치는 읽기만).

## 사례: 시야거리 슬라이더 (`tools/mod_view_distance.py`)

2026-10-03 빌드 `IsletEngine_release(x64).dll` (sha256 `8e4c0cd9…e3cf`) 분석 결과:

| 위치 (VA) | 내용 |
|---|---|
| `COption::UpdateViewDistance` 0x18012c7d0 | `[this+0x1ed8]` 단계 0..4 → 시야 `[+0x1f50]` 70/110/160/190/225칸, 물건 `[+0x1f54]` 30/40/50/60/70 (그 밖의 값은 180) |
| `COption::Load` → `UpdateData` 0x18012c5f0 | `user.xml` `<graphic>` id 3을 읽어 0..4로 자르고(스택 표 `[rsp+0x2c]=4`) UpdateViewDistance |
| 생성자 0x180129fea | 기본 단계 3 (`user.xml`이 없을 때) |
| `COptionUI::OnChangedViewRange` 0x180131f00 | ◀ ▶: 단계 0..4로 자르고 `"%d"`(단계+1)를 `[ui+0x160]` 글자에, UpdateViewDistance 후 월드 `IBrickGroupManager::SetViewRange(+0x230)` |
| `COptionUI::UpdateData` 0x180132140, `OnClickDefaultGraphic` 0x1801331a0 | 창을 열 때/기본값 — 같은 자르기·글자·UpdateViewDistance |
| `COptionUI::OnGuiEvent` 0x180130b30 | 이벤트 410(0x19a) 슬라이더: 노드 410 = 마우스 감도, 411 = 화각, 값 = `node->vt[0x420]()`×100 |
| 클라이언트 exe 0x140128490 | 월드 로드 때 `SetViewRange(option[+0x1f50])` — 영역 격자 = 1+시야/16 |
| `IBrickGroupManager::SetViewRange` 0x1800aa2b0 | 나선(`Nw::ITwirl`)을 `(1+시야/16)²×4`점으로 다시 만듦: 32칸 미만 16점, 70칸 100점, 225칸 900점 |
| `IBrickGroupManager::UpdateRegionAlloc` 0x1800aa070 | 매 프레임 나선을 32점씩 훑어 안 불러온 영역 하나를 불러옴. 한 바퀴 돌면 0..31을 훑는데, 나선이 32점보다 짧으면 `GetPoint` = NULL → **튕김** (원래 최소 70칸에선 생기지 않음) |

GUI 쪽: 노드 찾기 `root(ui+0x20)->vt[0xe0](id)`, 글자 `vt[0x388](text, 0)`, 슬라이더 값 읽기/쓰기
`vt[0x420]` / `vt[0x428](float 0..1)`.

패치(13곳): 단계를 0..100으로 넓히고(자르기 4→100 네 곳, 기본값 83),
UpdateViewDistance를 `시야 = 20 + 단계×2.05`, `물건 = min(시야, 30 + 0.4×단계)`로 바꾸고, 남은 자리에
슬라이더 처리(노드 4100 → `OnChangedViewRange(값×100, 1)`)를 넣었습니다. ◀ ▶·창 열기·기본값 세 곳은
UpdateViewDistance 대신 공통 도우미를 부릅니다: UpdateViewDistance → 옆 숫자를 실제 칸 수로 →
슬라이더 위치를 `(단계+0.5)/100`으로(이미 같으면 그대로 — 슬라이더 이벤트와 서로 부르며 돌지 않게).
도우미는 `.text` 끝의 빈 192바이트에 들어가고, `.text` 가상 크기를 파일 크기(0x204400)로 늘려 매핑합니다.
슬라이더 노드는 모드의 `Gui\option.xml`(그래픽 패널 아래 줄, id 4100, scroll_event 410)에 있습니다.
13번째 패치는 UpdateRegionAlloc의 한 바퀴 끝을 `min(32, 나선 점 수)`로 막습니다(12바이트:
`and [rdi+0x60],0 ; push 20h ; pop rbx ; cmp eax,ebx ; cmovl ebx,eax`).

영역(청크) 로딩: 클라이언트가 나선 안의 영역을 프레임마다 하나씩 `CBrickNetworkIO::SendRequestRegion`으로
요청하고, 로컬 서버 `IBrickServer::OnRecvRequestRegion`(0x180087970)은 거리 확인 없이 응답합니다. 플레이어가
영역을 옮기면 `UpdateRemoveRegion`이 나선 반경 밖을 내립니다. 즉 **로딩 반경 = 1 + 시야/16 영역** — 시야
최댓값을 올리면 로딩도 넓어집니다(1.1.0: 최대 512칸 → 반경 33영역, 나선 4356점). 그리는 쪽은
`CBrickWorld::BuildSceneGraph`(0x1800b62a0)가 컬링 동안만 카메라 원거리를 `시야 + 27.71`로 바꿨다 되돌리고,
실제 투영 원거리는 `CGameCamera::Create`(0x1800f8ad0)의 300칸 고정 → 14번째 패치로 하늘 카메라가 쓰는
1000.0 상수(0x18025630c)를 읽게 했습니다(높이 255에서 바로 아래 땅까지 약 255칸). `CBrickWorld+0x3e4`(60칸)는
가까운 영역 세부 처리용이라 그대로입니다.

로딩 속도(1.2.0): `UpdateRegion`(0x1800a9fb0)이 프레임마다 `UpdateRegionAlloc`을 한 번 부르고, 그 함수는 새
영역을 하나 할당하면 바로 끝나서(커서 = i+1) 프레임당 1영역이었습니다. 0x1800aa02d의 호출(11바이트)을
작은 반복 루틴 호출로 바꿔 `UpdateRegionAlloc`을 8번 부릅니다(각 호출은 저장된 커서에서 이어감). 루틴은
**아무도 부르지 않는 컴파일러 생성 export** `CSky::operator=(const CSky&)`(0x180033ba0, 924바이트) 앞 40바이트에
넣었습니다 — 클라이언트 import 목록·DLL 안의 call/jmp/RIP 상대 참조·재배치 포인터·.rdata RVA 어디에도 없음을
capstone 전수 검사로 확인. 남은 빈 자리를 찾을 때 같은 방법(`??0X@@...(const X&)`, `??4X@@` 복사 생성자/대입)이
쓸 만합니다.

로딩 속도(1.3.0): 할당 다음 단계는 영역 스레드입니다. NarewEngine2의 공통 스레드 루프(`IThreadProcess`
스레드 함수 0x18015a480)는 `Run(); Sleep(2)`를 반복하고, `CBrickWorld::Run`(0x1800b6050)은 캐시 확인
`FUN_1800a4a60` 1개 + 메시 만들기 `FUN_1800a48f0` 1개를 하고 각각 뒤에 `Sleep(1)` — 초당 수십~수백 영역이
한계였습니다(메인 스레드 쪽 `FUN_1800a4590`/`FUN_1800a46d0`은 다 된 영역을 한꺼번에 처리, 제한 없음).
1.3.0: `Run`의 두 호출(0x1800b6064, 16바이트)을 빈 자리 `SPARE+0x30`의 반복(각 16번, 대기열이 비면 바로
돌아옴)으로, 두 `Sleep(1)`(0x1800a4993, 0x1800a4b19)을 `Sleep(0)`으로, 프레임당 할당을 32개로. 총 패치 20곳.

검증: capstone으로 패치된 모든 구간 역어셈블, Windows 로더로 이미지 매핑 후 메모리 바이트 확인,
`tests/test_codepatch.py`. 게임 안 1차 시험(1.0.0, 12곳)에서 슬라이더를 30칸 아래로 두면 몇 프레임 뒤 튕김 →
게임이 남긴 미니덤프(`<게임>\Dump\*.dmp`, 예외 스트림 + 스택)로 위 원인을 찾아 1.0.1에서 고침.
1.0.1(슬라이더)·1.2.0(넓은 영역 로딩·빠른 할당)은 게임 안에서 확인됨. 32비트 클라이언트는 패치하지 않습니다(이 PC는
64비트 클라이언트만 실행). 적용 후 처음 켜면 기존 `user.xml`의 단계 3이 새 눈금으로 3%(약 26칸)가 되므로
슬라이더를 한 번 맞춰 주세요. 최댓값을 더 올리려면 `--max`로 다시 만들면 됩니다(엔진에 상한은 없지만 영역
격자가 커지고, 온라인에서는 서버가 보내 주는 영역까지만 보입니다).

## 사례: 프리캠 (`tools/mod_freecam.py`, DLL 두 개)

`python tools/mod_freecam.py <게임 폴더> <모드 프로젝트> [--speed 20 --fast 4]` — 같은 2026-10-03 빌드의
`NarewEngine2_Release64.dll`(sha256 `40d2a2fc…`)과 `IsletEngine_release(x64).dll`을 함께 패치하는 패치 파일 하나
(16곳) + `Gui\game_menu_top.xml`·`Gui\game_menu.xml` 버튼 두 개(노드/이벤트 id 31001 들어가기, 31002 나가기) +
`gui_skin\02_Mainmenu\Btn_FreeCam*.png` + `Language\word.xml` 병합(`MENU_FREECAM`, `MENU_FREECAM_EXIT`).

| 위치 | 내용 |
|---|---|
| NarewEngine2 `.data` 끝 0x180755c00 | 공유 상태: active, 나가기 버튼 표시 동기화, 카메라 초기화, 위치 xyz (`.data` 가상 크기 0x188e8→0x19000) |
| `IGUICore::UpdateEvent` 0x1800327e4 | 이벤트 큐 전달 직전에 이벤트 id(`vt[0x70]`)를 봄: 31001/31002면 상태만 바꾸고 게임 핸들러로 안 보냄 |
| `IGUICore::UpdateEvent` 0x1800326fc | 루트 루프 시작: 나가기 버튼 표시를 상태에 맞춤(`core->Find(31002)` `vt[0x100]`, `SetVisible` `vt[0xa8]`), 프리캠이면 그 버튼만 `UpdateEvent`(`vt[0x290]`) |
| `IGUICore::Render` 0x180032c39 | 프리캠이면 루트 대신 나가기 버튼만 `Render`(`vt[0x90]`) |
| `CGameCamera::Update` 0x1800f8d2e | 모드 분기(0 FPS / 1 RPG) 대신 새 루틴: 프리캠이면 마우스 회전(`UpdateMouseRotR/Lock`) → `at-eye`로 앞/옆 벡터 → 키(`CGameControl+0x5c+id`, W0 S1 A2 D3 Space4 Ctrl6 LShift9) → `SetEye`(`vt[0x78]`) + `SetYawPitchRoll(yaw, pitch, roll, 1)`(`vt[0xf8]`, 3인칭 각도는 SetMode처럼 yaw+180, 180−pitch) |
| `CGameControl` 0x1801002da·0x180100355·0x180100368·0x1800fed0f·0x1800fd268 | 키보드/마우스 잠금 검사(+0x262/+0x263)와 자동 이동(+0x261)에 프리캠 포함 → 채팅 중처럼 캐릭터가 멈춤 |
| `CGameControl::Update` 0x1800fd24d | ESC(뗌, `+0x91`==3) → 메뉴 대신 프리캠 종료 |
| 0x1800dcba7 · 0x1800f70db · 0x1800f56b9 | 이름표(`AddDrawName`)·말풍선·숫자 효과 렌더가 "그릴 것 없음" 쪽으로 |

IsletEngine 쪽 코드는 `Nw::Interpolate` import(IAT 0x180206530)로 NarewEngine2 주소를 얻고, NarewEngine2 코드 동굴
첫 8바이트 `NWFRCAM1`을 확인한 뒤에만 상태를 씁니다. 코드 동굴: `IGUICore` 복사 생성자(N2 0x180024bf0),
`COption` 복사 생성자(IE 0x1800fb310) — 둘 다 아무도 부르지 않음(같은 capstone 전수 검사). 게임이 쓰는 GUI 코어는
`CGUICore`(`IGUICore::CreateDefault`)지만 Update/UpdateEvent는 `IGUICore` 구현을 그대로 불러서 위 위치가 맞습니다.
청크 로딩은 따로 고칠 필요 없음: `IBrickGroupManager::UpdateRegion`이 월드 카메라(world+0x308)의 `GetAt()`을 중심으로
쓰는데, 프리캠에선 at = 눈 앞 1칸. 플레이어 쪽 영역이 내려가도 `ICreature::UpdateFalling`은 안 불러온 영역에서
중력을 쓰지 않습니다. 2026-10-15 게임 안에서 확인됨(버튼·이동·GUI 숨김·청크 로딩·복귀).
