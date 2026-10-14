# AI 에이전트로 NarewStudio 쓰기

NarewStudio의 모든 기능을 AI 에이전트(Claude Code 등)가 **직접** 쓸 수 있다. 에이전트에게 "내 월드 스폰 옆에
작은 집 지어 주고 침대랑 의자 넣어 줘", "인벤토리 창 닫기 버튼을 왼쪽 아래로 옮겨 줘", "클릭 소리 짧게 잘라
줘"처럼 말하면, 에이전트가 맵을 보고(ASCII 지도·그림) 고친 뒤 모드 프로젝트에 저장한다.

## 1. 연결

| 에이전트 | 방법 |
|---|---|
| **Claude Code** | 이 저장소 폴더(`NarewTools`)에서 실행하면 `.mcp.json`의 **narew-studio** 서버가 잡힌다. 처음 한 번 사용 승인. 도구 이름은 `mcp__narew-studio__…` |
| 다른 MCP 클라이언트 | stdio 서버 `python -m studio.agent mcp` (작업 폴더 `NarewTools`, 또는 `PYTHONPATH`), 배포판은 `NarewAgent.exe mcp` |
| 명령줄만 되는 에이전트 | `python -m studio.agent call <도구> 키=값 … --pretty`, 여러 단계(월드 세션 유지)는 `run steps.json`, 계속 주고받기는 `serve` (JSON 한 줄씩) |

- 도구 목록: `python -m studio.agent tools`, 사용법: `python -m studio.agent guide world` (overview, world, content, gui, sound, models, mod, safety)
- 에이전트용 안내는 `AGENTS.md`(Claude Code는 `CLAUDE.md`로 자동으로 읽음)와 MCP 서버의 instructions에 들어 있다.

## 2. 무엇을 할 수 있나 (도구 113개)

| 묶음 | 도구 | 내용 |
|---|---|---|
| core | `status` `project_new/open/info/revert/export_zip` `studio_view` `studio_show` `guide` | 게임 폴더·모드 프로젝트, 창이 지금 보여 주는 것, 창에 결과 띄우기 |
| world | `world_open` `world_layer` `world_section` `world_heightmap` `world_ground` `world_render`(위·3D·전체) `world_fill`(꽉/속 빈/벽/테두리) `world_build`(ASCII 설계도) `world_shape`(구·돔·원기둥·원뿔·피라미드·선) `world_copy/paste`(회전, 가구 포함) `world_place(_many)` `world_switch`(조명 켜기·끄기) `world_rotate`(놓인 가구 돌리기) `world_image`(그림 → 블록 도트) `world_delete_objects` `world_set_start` `world_undo/redo` `world_save` `world_save_prefab` `world_capture` `world_new`(최대 4096×4096 새 평지 월드) `world_apply_to_game`(새 월드로 추가 `new_world`, 크기 바꾸기 `resize`) `world_restore_backup` `world_remove_from_game` | 맵·프리팹 편집 전부. 그림과 ASCII로 확인하며 작업 |
| content | `bricks_list` `products_list` `items_search` `content_new_block/furniture/item/sound` `content_added` `block_tile*` `furniture_textures` | 새 블록·가구·아이템·사운드, 블록 아틀라스, 가구 텍스처 |
| files / tables | `files_search` `text_read/write/edit` `file_import/export/revert` `image_view/adjust` `texture_make` `table_records/upsert/remove` `string_get/set` `pack_extract/verify/build` `packs_list` | 모든 리소스, XML 표 레코드 병합, 문자열, 팩 |
| gui | `gui_list` `gui_tree` `gui_render` `gui_edit` `gui_textures` `gui_revert` | 화면 레이아웃: 노드 번호가 찍힌 그림을 보고 옮기기·크기·글자·색·그림·추가·삭제 |
| sound | `sound_list` `sound_info`(파형 그림) `sound_edit` `sound_import` `sound_export` | 자르기·페이드·볼륨·정규화·거꾸로·속도, 다른 파일로 교체(형식 자동 맞춤) |
| models | `models_list` `model_info` `model_render` `model_export_gltf` `model_import_gltf/new` `anim_import_gltf` `anim_speed` | 모델 그림, Blender 왕복, 애니메이션 속도 |
| manager | `manager_status` `manager_install_project` `manager_set_mod` `manager_apply` `manager_restore` | 모드 매니저 |

## 3. 열려 있는 NarewStudio 창과 함께 쓰기

- 에이전트와 창은 같은 설정(게임 폴더·모드 프로젝트)을 쓴다. 에이전트가 프로젝트를 만들거나 열면 창도 따라간다.
- 에이전트가 저장하면 창이 **1초 안에 다시 읽는다**. 월드 에디터는 카메라 위치를 그대로 두고 다시 그린다.
  창에 저장하지 않은 편집이 있으면 덮어쓰지 않고 상태 표시줄에 알린다.
- 에이전트는 `studio_view`로 창이 지금 보여 주는 월드·카메라 위치·마우스 아래 칸·GUI 선택을 알 수 있다
  ("여기에 지어 줘"가 통한다). `world_open`을 인자 없이 부르면 창에 열린 월드를 연다.
- `studio_show`로 에이전트가 결과 화면(월드·GUI·리소스 …)을 창에 띄운다.
- 연결은 `문서\NarewStudio\agent\`의 파일 두 개(events.json, gui.json)로 한다. 네트워크·포트는 쓰지 않는다.
  창과 같이 쓰는 설정(`문서\NarewStudio\settings.json`)과 설치한 모드(`문서\NarewStudio\ModManager\`)도 문서 폴더에 있다. Claude 데스크톱 앱 같은 Windows 패키지 앱에서 실행된 에이전트는 AppData에 만든 파일을 자기만 보는 사본에 쓰기 때문이다.

## 4. 안전장치

- 게임 폴더는 읽기만 한다. 모든 변경은 모드 프로젝트에 들어가고, 게임에는 모드 매니저로만 들어간다.
- 게임을 실제로 바꾸는 도구(`world_apply_to_game`, `world_restore_backup`, `world_remove_from_game`, `manager_apply`, `manager_restore`)는
  `confirm=true` 없이 부르면 **무엇이 바뀌는지만** 알려 준다. 에이전트는 사용자에게 물어본 뒤에만 confirm한다.
  게임이 켜져 있으면 거부하고, 바꾸기 전에 백업한다.
- 내 월드 저장본(`Local\*.bwr`)은 `world_save`가 절대 덮어쓰지 않는다 (`문서\NarewStudio\Worlds\<월드>_edit.bwr`
  같은 새 파일). 게임 폴더 안으로 저장·추출하는 것도 거부한다.
- 모든 편집은 되돌리기 단계가 하나씩 쌓인다(`world_undo`). 저장 전에는 파일이 바뀌지 않는다.

## 5. 맵 편집 요령 (에이전트가 따르는 규칙)

- 좌표: x·z 수평, y 위(0~255). ASCII와 위에서 본 그림은 **x 오른쪽, z 아래쪽**.
- 블록 0 = 공기, 1~254 = 블록 종류, 255 = 가구 밑 충돌 칸(보이지 않음, 직접 칠하지 않음). 편집 도구는 기본으로
  255를 건드리지 않고, 가구를 지우면 그 가구만 쓰던 255를 함께 지운다(3D 에디터와 같은 규칙 — 같은 코드).
- 가구 높이: 게임에 들어 있는 맵은 가구가 받치는 블록 바로 위(블록 y + 1)에 있는 경우가 대부분(약 90%)이다.
  내 평평한 월드(바닥 #60이 y=0)에서는 게임이 직접 놓은 보물상자·의자가 모두 y=2(바닥 + 2)에 있다.
  `world_place`는 기본으로 블록 바로 위에 놓고, 근처에 다르게 놓인 게임 오브젝트가 있으면 결과에 알려 준다.
  직접 깐 바닥 위(금색 정자 바닥 y=1 → 가구 y=2)는 인게임에서 보이는 것을 확인했다.
- **인게임 확인 (2026-10-14):** 에이전트가 이 도구만으로 내 월드(lj) 스폰 옆에 지은 거대한 영국식 성(해자·탑·성문·4층 본성·
  대연회장·막사, 가구 106개)을 "내 월드에 적용"으로 넣었고 게임에서 잘 보였다.
  **아직 확인하지 않은 것:** 맨 #60 바닥 위 y=1에 놓은 가구가 게임에서 제 높이로 보이는지.
- 조명: 종류(event)가 "on"인 설치물(램프·촛대·가로등 …)은 저장된 오브젝트 상태 바이트의 **0x10 비트(IsUse)**가
  켜져 있어야 게임에서 빛난다(빛은 모델 .nwmt의 `<light_list>`). 게임에 들어 있는 맵의 조명은 대부분 상태 16이다.
  `world_place`·`world_place_many`는 조명을 **켠 채로** 놓고(`on=false`면 끈 채로), `world_objects`는 조명마다
  `on`을 보여 주며, `world_switch(ids 또는 box, on=true/false)`로 이미 있는 조명을 켜고 끈다(다른 비트는 그대로).
- 놓인 가구 돌리기: `world_rotate(ids 또는 box, rot=0~3 또는 turn=칸 수)` — 놓인 칸을 중심으로 게임의 R 키처럼 돌린다.
  충돌 칸이 함께 옮겨지고 오브젝트는 그대로 남는다(보관함 속 아이템, 조명 켜짐/꺼짐 유지). 돌린 자리가 막히면 `failed`에 나온다.
  3D 에디터에서 마우스로 놓은 조명도 저장할 때 켜진 채로 들어간다. 복사·붙여넣기는 켜짐/꺼짐을 그대로 옮긴다.
- 벽에 거는 설치물(벽등 등)의 회전: rot 0 = 벽이 기준 칸의 +z 쪽, rot 1 = +x, rot 2 = -z, rot 3 = -x
  (게임에 들어 있는 맵에서 확인).
- 그림을 블록 도트로: `world_image(image=그림 파일, at=상자의 낮은 모서리, width=가로 칸 수, facing=…)`.
  픽셀마다 색이 가장 가까운 블록(CIE Lab)을 고른다. 게임 공간은 왼손 좌표계라 +z를 바라보면 +x가 오른쪽이고,
  `facing="-z"`(기본)는 z가 작은 쪽에 선 사람에게 바로 보인다. `"+z"` `"-x"` `"+x"`는 다른 방향 벽, `"up"`은 바닥
  (위에서 보면 +z 쪽이 그림 위). 벽은 기본으로 양면(뒤에 좌우를 뒤집은 층 하나 더, 두께 2칸)이라 뒤에서도 글자가
  바로 읽힌다. `palette="plain"`(기본)은 무늬가 심한 블록을 빼고, `bricks=[…]`로 직접 고를 수 있다(유리·물·식물·반 칸·
  흙·모래·못캐는 블록은 쓰지 않는다). 투명한 픽셀은
  그 칸을 그대로 둔다. `dry_run=true`로 미리보기 그림(원본 | 블록)부터 본다.
  **인게임 확인 (2026-10-14):** 내 월드에 세운 128×128 양면 고양이 그림이 게임에서 잘 보였다.
- 모드로 추가한 가구가 있는 저장본: 지금 게임 폴더의 표에 없는 가구(모드 의자 등)가 있는 영역은 오브젝트를
  읽지 못해 그대로 두고 목록에 나오지 않는다(`world_clear`로도 지워지지 않고, 저장하면 읽은 그대로 남는다).
  이제 조용히 넘어가지 않고 알려 준다: `world_open`·`world_info`에 `objects_unreadable_regions`(영역 수, 처음 몇 개의
  [rx, rz], 그 안의 오브젝트 수, 표에 없는 설치물 번호와 자리, 저장본이 다른 설치 폴더의 것이면 `played_in`)와
  한국어 `hint`가 붙고, 상자가 그런 영역에 걸치면 `world_clear`·`world_delete_objects(box)`·`world_objects`·
  `world_stats`·`world_copy` 결과에 같은 이름의 `note`가, `world_save`에는 "그대로 저장했다"는 알림이 붙는다.
  3D 에디터는 그런 월드를 열면 경고 창을 한 번 띄우고(월드·게임 폴더마다 한 번) 제목에 "⚠ 가구 일부를 읽지 못함"을
  남긴다(마우스를 올리면 자세히). 설치물 편집 도구로 그 영역을 클릭해도 이유를 알려 준다.
  고치려면 그 저장본을 플레이한 게임 폴더로 연다: 창은 위쪽 게임 폴더 버튼, 에이전트는 `set_game` 또는
  명령줄 `--game "<Steam 설치 폴더>" --no-follow`.
- 큰 월드(1024×1024)도 바로 열린다. 읽기는 필요한 영역만 풀고(최근 512영역만 기억), 그림은 512×512칸까지.

## 6. 개발·테스트

| 경로 | 내용 |
|---|---|
| `studio/agent/` | `registry.py`(도구 등록·인자 검사), `tools_*.py`(묶음별 도구), `context.py`, `render.py`(그림), `mcp.py`(MCP 서버, 표준 라이브러리만), `cli.py`, `live.py`(창 연결), `guide.py` |
| `studio/worldedit.py` | 화면 없는 월드 편집 세션 + 3D 에디터와 함께 쓰는 가구 규칙 |
| `studio/ui/agentlink.py` | 창 쪽 연결: 변경 다시 읽기, 화면 열기 요청, 상태 알림 |
| `tests/test_agent.py` | 가짜 게임 폴더에서 모든 묶음 + MCP 프로토콜 + CLI (276개 확인) |
| `tests/test_agent_live.py` | 실제 창 + 별도 에이전트 프로세스: 월드 다시 읽기(카메라 유지), studio_view/show, 프로젝트 따라가기 |

```bash
python tests/test_agent.py "<게임 폴더>" D:\work\t_agent
```
```bash
python tests/test_agent_live.py "<게임 폴더>" D:\work\t_live
```

빌드(`python tools\build_exe.py`)는 `NarewAgent.exe`(콘솔)를 함께 만들고, MCP 인사(initialize + tools/list)로 자체 점검한다.
