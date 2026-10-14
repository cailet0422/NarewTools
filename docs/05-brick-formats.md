# 브릭(복셀) 포맷 `.iaf` / `.bwr` — 확정 사양

> 상태: **완전 해석 완료.** 출고된 `.iaf` 56개와 `.bwr` 29개를 전부 디코드했고, 다시 인코드한 결과가
> **원본과 바이트 단위로 동일**하다. 모든 브릭 스트림을 강제로 재압축해도 동일하다.
> 오브젝트(나무·설치물) 10,629 + 794개를 전부 개별 레코드로 분해했다 (원본 그대로 보존한 영역 0개).
> 근거: `IsletEngine_release(x64).dll` (SHA-256 `8e4c0cd9…e3cf`) Ghidra 12.1.4 디컴파일,
> REA evidence 번들 `re/rea-evidence-bricks.json` (40 레코드). 아래 `ev_…`는 그 번들의 Evidence ID 앞부분.
>
> 표기: **관찰** = 디컴파일 코드나 파일 데이터에서 직접 확인, **추론** = 정황상 결론(검증 필요).

## 1. 공통: 브릭 1개 = 1바이트

| 값 | 의미 | 근거 |
|---|---|---|
| 0 | 공기(빈 칸) | 관찰: `IBrickGroup::SetLocalBrick`(`0x1800a80f0`)가 0을 "비움"으로 처리하고 높이맵을 낮춤 |
| 1–254 | `XML/brick_table.xml`의 `<brick id>` | 관찰: `CBrickTable::GetType(id)` = `table + id*0x160` 직접 인덱스, `ParsingBrick`은 id 1–255만 허용 (`0x1800af7f0`/`0x1800af900`, ev_fa860475) |
| 255 | 보이지 않는 충돌 채움 브릭 | 관찰: `SetLocalBrick`(`0x1800a8200`)이 0xFF를 따로 취급. 추론: 설치물 충돌 영역 — 출고 맵의 255 셀 565개 중 557개가 설치물 위치 3칸 이내 |

- `Islet::SBrick`은 1바이트 구조체다 (관찰: `CBrickMemoryIO::GetBrick`이 1바이트를 반환, ev_599a30c0).
- 브릭은 **가로 16 × 높이 H × 세로 16 기둥**(엔진 용어로 group / region) 단위로 저장된다.
- 기둥 안 인덱스 = **`(y * 16 + z) * 16 + x`**, y가 위쪽 (관찰: `IBrickGroup::GetLocalBrick` `0x1800a8060`,
  `IBrickServerRegion::GetBrick` `0x180093c70`, ev_599a30c0 / ev_6ae31f20).
- 메모리에서는 데이터 뒤에 256바이트 높이맵(열마다 가장 높은 브릭의 y)이 붙지만 **파일에는 저장하지 않는다**.
  로드 후 다시 계산한다 (관찰: `IBrickGroup::CreateData` `0x1800a77f0`, region `UpdateHeightMap`).
- 좌표계: x 오른쪽, y 위, z 앞쪽(D3D 왼손 좌표계). MagicaVoxel(Z 위, 오른손)로 내보낼 때는
  `vox (x, y, z) = 게임 (x, z, y)`로 y·z만 바꾼다. 이 변환으로 물리적 배치가 그대로 유지된다
  (확인: `logotext01.iaf`를 정면에서 렌더링하면 "ISLET" 글자가 바르게 읽힘).

## 2. `.iaf` — 건물/나무 프리팹 (`Islet::CBrickArchitecture`)

로더 `CBrickArchitecture::Load` `0x1800a0130`, 세이버 `Save` `0x1800a0340`, 생성 `Create` `0x18009fd90` (ev_f769a54d).

| 오프셋 | 타입 | 이름 | 의미 |
|---|---|---|---|
| +0x00 | u32 | magic | `0x203` |
| +0x04 | i32 | width | x 크기 (16의 배수) |
| +0x08 | i32 | height | y 크기 (기둥 높이 H) |
| +0x0C | i32 | depth | z 크기 (16의 배수) |
| +0x10 | i32 | group_count | `(width/16) * (depth/16)` — 로더가 일치하지 않으면 실패 |
| +0x14 | … | groups | group_count번: `u32 size` + zlib 스트림 `size` 바이트 → 풀면 `H*256` 바이트 |
| 끝 | char[128] | trailer | 128바이트 버퍼를 그대로 기록. 로더는 앞 2바이트를 0으로 만들고 읽음 (내용은 초기화 안 된 메모리 — `0xCD` 등) |

- 그룹 순서: **z가 바깥, x가 안쪽** — 그룹 `i`는 `(gx, gz) = (i % (width/16), i / (width/16))`.
  관찰: `Create`의 이중 루프(바깥 z, 안쪽 x). 데이터로 확인: `logotext01.iaf`(112×64×64, 그룹 28개)가 끊김 없는 글자로 나옴.
- 헤더 순서는 `width, height, depth`인데 엔진 멤버는 `+0x90=width, +0x98=height, +0x94=depth`로 저장 순서와 다르다 (관찰).
- 압축: 출고본 전부 zlib 레벨 6 (`78 9C`)으로 재현된다.

용도 (관찰): `XML/tree.xml`의 나무 성장 단계(`tree_0N_0M.iaf`), `default/character_select.iaf`(캐릭터 선택 화면),
`Architecture/building/*`(건물 프리팹, XML 참조 없음 — 코드나 서버에서 사용하는 것으로 추론).

## 3. `.bwr` — 월드 전체 (`Islet::IBrickServer` / 클라이언트 `IWorldFile`)

서버 저장 `IBrickServer::SaveTo` `0x180048680`, `SaveHeader` `0x1800488c0`, `SaveTail` `0x180048920`,
서버 로드 `LoadRegions` `0x1800451a0` → `CreateRegions` `0x1800459c0` (ev_d5656987, ev_8472c082).
클라이언트(로비/미리보기) 로드 `IWorldFile::Create` `0x1800606d0`, `IWorldFileRegion::Create` `0x180060330`
(파일 이름 `BrickServer\BrickServerMapFile.cpp`, ev_392ca926). `IBrickGroupCache::CreateBwr`가 이것을 쓴다 (ev_be55aa4e).

```
+-----------------------------+ 0
| 헤더 16바이트                |
+-----------------------------+ 16
| region × N (순차, 가변 길이) |  region i = (rx, rz) = (i % (W/16), i / (W/16))
+-----------------------------+
| tail (서버 상태)             |
+-----------------------------+ file_size - 4N
| u32 region_offset[N]        |  각 region 시작의 절대 오프셋 (SaveTo가 마지막에 기록)
+-----------------------------+ file_size
```

### 3.1 헤더

| 오프셋 | 타입 | 의미 |
|---|---|---|
| +0x00 | u32 | magic `0x870203` (구버전 `0x870202`는 아래 두 필드가 없음 — 로더는 둘 다 허용, 출고본은 전부 `0x870203`) |
| +0x04 | i32 | width (x, 16의 배수) |
| +0x08 | i32 | depth (z, 16의 배수) |
| +0x0C | i32 | region 수 = `(W/16)*(D/16)` (로더는 읽고 버림) |

region 하나는 **16 × 256 × 16** 브릭이다 (높이는 항상 256). 1024×1024 맵 = region 4096개.

### 3.2 region 레코드 (`IBrickServerRegion::SaveToFile` `0x180094f10` / `LoadFromFile` `0x180094650`, ev_0e6bcd09)

| 타입 | 이름 | 의미 |
|---|---|---|
| i64 | version | region 변경 버전 (로드 시 1 미만이면 1) |
| i32 | size | 브릭 스트림 크기 |
| u8[size] | bricks | zlib → 65,536바이트 (`256 << 8`) |
| i32 | n_special | 특수 브릭 목록 개수 |
| u32[n_special] | special | 각 4바이트 `x, y, z, brick` (아래). **n ≥ 256이면 서버는 목록을 건너뛰고 0개로 취급** (관찰) |
| i32 | n_object | 오브젝트 수 |
| object[n_object] | objects | 3.3 참고 |
| i32 × 3 | 0, 0, 0 | 예약 (항상 0) |

special 4바이트: `x`(0–15), `y`, `z`(0–15), `brick`. 관찰: 출고 맵 957,353개 전부 x·z < 16이고, 4번째 바이트는
대부분 그 위치의 브릭(잡초 15, 잎 46–48, 꽃 16–19, 물 249 등)과 같다. 추론: 시간에 따라 갱신되는 브릭
(식물 성장, 물 흐름, 잎 소멸)의 대기 목록. 실행 중의 별도 3바이트 목록(`IsSpecial` `0x180097530`)과는 다르다.

### 3.3 오브젝트 레코드 (`IWorldObject::SaveBinary` `0x1801ce000` / `LoadBinary` `0x1801ce0e0`, ev_a05df9d3)

공통 머리 30바이트 — 모든 클래스가 같은 함수를 쓴다 (vtable 슬롯 53/54가 전 클래스 공통, 관찰):

| 타입 | 멤버 | 이름 | 의미 (getter 근거, ev_802843e9) |
|---|---|---|---|
| u8 | GetType() | kind | 1 = 나무(`ITreeObject`), 2 = 설치물(`CProductServer` 계열) — `IBrickServer::CreateObject` `0x180047f50` |
| i32 | +0x3C | type_id | 나무: `tree.xml`의 tree id (1 과실, 2 소나무, 3 참나무, 4 정글, 5 아카시아, 6 선인장). 설치물: `product.xml`의 product id |
| u16 ×3 | +0x28 | x, y, z | **월드 좌표** (관찰: 출고 맵 오브젝트 11,471개 전부 소속 region 안). 나무는 뿌리 브릭 위치 (그 칸 브릭 = `33 + type_id`, 나무 10,629개 중 10,627개 일치) |
| u8 | +0x2E | rot | `GetRot()` — 0–3 (90° 단위, 추론) |
| u8 | +0x2F | state | `GetState()` — 비트 3 = `IsLock`, 비트 4 = `IsUse` |
| u32 | +0x30 | age | `GetAge()` |
| i32 | +0x34 | hp | 로드 시 `[1, GetMaxHP()]`로 잘림 |
| i64 | +0x40 | owner | 소유자 ID (추론: 캐릭터/계정 ID) |
| … | | extra | 클래스별 추가 데이터, 아래 규칙 |

**extra 크기 규칙** — 엔진의 `IProductType::SeekExtraBin` `0x18018ca80` (ev_060d2f0d). 클라이언트가 내용을 몰라도
건너뛸 때 쓰는 함수라서 파싱 규칙으로 그대로 쓸 수 있다. 분기 기준은 `product.xml`의 `<event>` 이름을
`PRODUCT_TYPE` 번호로 바꾼 값(이름 표 `0x180398d90`, 72개)과 `<level option>` 비트다 (`IProductType::ParsingType` `0x18018bf90`, ev_a12fadfa).

| 대상 | extra 형식 |
|---|---|
| 나무 (kind 1) | `u16 n` + n바이트. 출고본은 n=5: `u8 level, u8×4` (`ITreeObject::SaveExtraBin` `0x1801c9990`) |
| event 5 board, 24 photo, 25 movie, 30 kitchen_garden, 39 auto_craft, 42 windmill | `u16 n` + n바이트 (클래스가 n에 자기 크기를 기록 — 예: 표지판 0x4C/0x58) |
| event 6 storage, 22 feed, 29 water_tank, 32 cloth_dummy, 33 item_holder, 35 fish_tank, 36 flower_pot, 37 decor_obj, 41 mailbox, 52 generator, 53 logic_gate | `u8 slots, u8 has_color, [ARGB×3 = 12], slots × (u8 있음, [아이템])`. mailbox는 뒤에 `0x48 + slots*72`바이트 추가 |
| event 28 store | `0x62`바이트 + `u16 n` + n × (u8 있음, [아이템 + 9 + 0x38바이트]) |
| 그 밖의 event (none, on, door, sitdown, treasure, …) | `option & 2`(색 지정 가능)이면 ARGB×3(12바이트), 이어서 `u16 n` + n바이트 (출고본은 n=0) |

아이템 레코드 (`IItem::SaveBin` `0x1801471b0` + `CServerItem::SaveBin`, ev_21b90f86):
`i32 item_id, u16, u16 count, u8, u8, u8 0, u8 extra_type, [IItemExtra], i32×4, u8, u8, i32, i32, i64, i64`
— extra_type이 0이 아니면 `IItemExtra::Create(type)`의 가변 데이터가 붙는다 (출고본에는 없음, `narew`는 미해석으로 처리).

설치물 클래스 선택 (`CProductManagerServer::CreateObject` `0x1801993e0`, ev_aa9004db): event 1 → Light(option 0x10000이면 StageLight),
2·3 → Chair, 4 → Door, 5 → SignPost, 6 → Storage(option 0x400이면 StoragePassword), 21 Bomb, 22 Storage, 23 Food, 24 Photo …,
기본값 → `CProductServer`(option 0x10000이면 FineTunning). 클래스별 `LoadExtraBin`도 디컴파일해 위 규칙과 일치함을 확인했다
(Storage/WaterTank/SignPost/Photo/Windmill/AutoCraft/Generator, ev_a75e56cb / ev_5608c06c).

### 3.4 tail (마지막 region 뒤 ~ 오프셋 표 앞)

두 가지 배치가 출고본에 섞여 있다. `narew`는 tail을 **바이트 그대로 보존**하고 알려진 필드만 보여 준다.

| 배치 | 해당 파일 | 앞부분 |
|---|---|---|
| `mapfile` | 64_0, 96_0, 128_0, 64/96/128_flat2, halloween/new_01/normal/winter_map, scene 96_0 | `i64 a, i64 b, i64 c, float start_pos[3]` — `IWorldFile::Create`가 정확히 이만큼 읽고 닫는다. c는 저장 시각(unix)인 경우가 있음 |
| `server` | *_flat 전부, 160–256의 _0·_flat2, 로비 3종 | `SaveTail` 형식: `i64 object_serial, [공(ball) 목록], [게임 모드], [낚시 대회], [대회 목록], u8 1, i32 seed, u8×3, i64 save_time, float start_pos[3]` |

두 배치 모두 이어서: CSkyTimer 레코드 `u8 0x22, i32, i32, u32 time_of_day_초` → `u32 0x771231` 표지 + `i32 n` + n×44바이트
목록(`0x18004e250`) → 16바이트 → 땅 소유권 관리자 데이터 → `i32 0` 또는 `u32 0xFC8818C8` 블록 (`0x18006a7e0`).
`narew`는 `0x771231` 표지를 기준으로 start_pos·하늘 시간을 찾는다 (29개 파일 전부 위치 확인).

용도 (관찰): `XML/private_server.xml` → 개인 서버 맵 템플릿 21종(64–256, `_0`/`_flat`/`_flat2`), `XML/lobby.xml` →
`spring_lobby.bwr`, `new_01.bwr`, `scene/*.scene`·`tutorial.xml` → `scene/96_0.bwr`.

### 3.4.1 새 설치물 쓰기 (월드 에디터 "🪑 설치물", `narew.brick.new_product` / `product_footprint`)

출고 맵의 설치물 809개를 조사해 정한 규칙이다(2026-10-13).

- 오브젝트 머리: `kind 2, type_id, x/y/z = 놓는 칸, rot 0–3, state 0, age 0, hp = product.xml <stat hp>`(809/809 일치),
  `owner 0`(출고 맵의 owner는 0–11의 작은 수).
- extra: 일반 설치물은 `[option & 2이면 0×12 색] + u16 0`(출고본 761개 전부 이 모양, 색은 전부 0).
  보관함 계열은 `u8 칸수 = <level value>, u8 0, 칸수 × u8 0`(빈 칸 — 예: 선물상자 #175 level 4 → 4칸).
  표지판·사진·영상·텃밭·자동 제작·풍차(크기 붙은 자체 데이터), 상점, 우편함은 아직 만들지 않는다.
  → product 937종 중 857종(91%)을 놓을 수 있다.
- **충돌 칸(브릭 255)**: 놓인 칸의 중심을 원점으로 `<min>/<max>` 상자를 `rot × 90°` 돌리고
  (`(x, z)` → rot1 `(z, −x)`, rot2 `(−x, −z)`, rot3 `(−z, x)`), 칸 중심이 상자 안에 드는 칸을 255로 채운다 —
  가로·세로 `d ∈ [min, max]`, 높이 `dy + 0.5 ∈ [min_y, max_y]`, 한 축이 비면 0. 출고 맵의 외따로 놓인 설치물 66개(+ 내 월드 저장본)
  전부와 칸 단위로 일치 (`tests/test_footprint.py`).
- 에디터는 새 오브젝트와 충돌 칸을 한 번의 되돌리기 단위로 넣고, 설치물을 지우면 다른 설치물이 쓰지 않는 충돌 칸을 비운다.
  저장한 파일은 엄격 모드(`strict=True`)로 다시 읽어 모든 오브젝트가 엔진 규칙대로 풀리는지 확인했다(`tests/test_place_products.py`).
- **2026-10-14 인게임 확인**: 사용자 월드 `lj`에 블록(금·콘크리트·유리)과 새 설치물(나무침대·나무의자·보물상자, 충돌 칸 포함)을 넣은 정자를
  "내 월드에 적용"으로 저장본에 넣고 게임에서 그대로 보이는 것을 확인했다.

### 3.5 게임이 저장하는 내 월드 (`IBrickServer::Save` `0x180048020` / `Load` `0x180044b10`)

내 PC의 로컬 서버(싱글 플레이, 내가 연 서버)는 월드를 게임 폴더에 세 파일로 저장한다. `Save`는 `.npc` → `.lnd` → `.bwr` 순서로
쓰므로 `.bwr`이 마지막이다.

| 파일 | 내용 | 쓰는 곳 |
|---|---|---|
| `Local\<월드>.bwr` | 블록 월드 전체 (이 문서의 `.bwr` 형식, map.pack의 맵과 같음) | `this` vtable +0x2f0 |
| `Local\<월드>.npc` | NPC | `this+0xbc8` 관리자 |
| `Local\<월드>.lnd` | 별도 하위 시스템(`this+0xc68`). 1024×1024 월드에서 32,776바이트 머리 + 칸마다 1바이트 — 내용 미해석 | `this+0xc68` vtable +0x70 |
| `backup_map\map_<월>_<일>_<시>.bwr` | `Save(…, backup=1)`일 때 같은 `.bwr`를 한 시간 단위 이름으로 한 벌 더 | |

- 사용자 월드 `lj.bwr`(2026-10-13)도 `narew brick`/`narew.brick.World`로 읽히고 바이트 단위로 재빌드된다.
- 다른 경로 문자열: `private_server/%d.bwr`, `private_server/%d/%d.bwr`(+`.npc`) — 여러 월드를 쓰는 서버용으로 보이며 미확인.
- NarewStudio 월드 에디터의 "💾 내 월드"와 월드 캡처 창(`studio/savedworlds.py`)이 이 파일들을 읽는다. 도구는 게임 저장 폴더에 쓰지 않는다.
- 다른 사람이 연 서버의 월드는 그 서버에만 있다. 클라이언트에 코드를 넣어 받아 오는 기능은 만들지 않는다.

### 3.6 월드 크기 — 1024보다 큰 월드 (2026-10-14)

게임의 "맵 만들기"(`xml\createmap.xml`의 지형 스크립트 10종)는 항상 1024×1024를 만든다. 크기는 지형 스크립트에 없고,
`IBrickWorldInfor` 생성자(`0x18009e0e0`)가 `0x400 × 0x400`으로 고정한다. `IBrickWorldGenerator::Create`(`0x1800b9650`)도
기본값 그대로인 새 `IBrickServer`에 지형을 만든다.

하지만 **저장된 월드를 열 때는 `.bwr` 머리의 가로·세로를 그대로 쓴다** (관찰):

| 함수 | 주소 | 내용 |
|---|---|---|
| `IBrickServer::LoadRegions` | `0x1800451a0` | magic `0x870203`이면 width·depth를 읽어 `SetWorldSize(width, 256, depth)` |
| `IBrickServer::SetWorldSize` / `CreateRegions` | `0x1800447d0` / `0x1800459c0` | region 포인터 배열, 서식지, 땅 소유권을 그 크기로 새로 만든다 |
| `IBrickServerRegion::Create` | `0x1800929c0` | region마다 65,536바이트 블록 + 약 2.3KB를 **항상** 잡는다 → region당 약 69KB |
| `ILandOwnershipMgr::Create` / `Load` | `0x180114e00` / `0x180114f50` | 땅 = 32×32×32 블록, 번호를 i16에 저장. `.lnd`는 magic `0x850490` + 개수가 맞지 않으면 읽지 않는다 (크기가 바뀌면 비어서 시작) |
| `IBrickWorldInfor::GetPositionToID` | `0x18009e130` | `x + W*z + W*D*y`를 int로 계산 |
| `CBrickWorld::Create` / `ResizeWorld` | `0x1800b3bc0` / `0x1800b4510` | 클라이언트는 region 포인터 배열(8바이트 × region 수)만 잡고, 블록은 근처만 캐시 |
| `CBrickWorld::LoadMinimap` / `UpdateMinimap` | `0x1800b42f0` / `0x1800b69f0` | 미니맵 = 월드 크기 그대로의 텍스처 한 장(`Cache\<주소>.png`). 텍스처가 더 작으면 비율로 줄여 그린다 |

| 크기 | region | 서버 RAM(블록) | 미니맵 텍스처 | 위치 번호 넘침 | 땅 번호(i16) |
|---|---|---|---|---|---|
| 1024 | 4,096 | 약 0.27 GB | 4 MB | 없음 | 8,192개 |
| 2048 | 16,384 | 약 1.0 GB | 16 MB | 없음 | 32,768개 (딱 맞음) |
| 4096 | 65,536 | 약 4.2 GB | 64 MB | y ≥ 128 | 131,072개 — 넘침 |
| 8192 | 262,144 | 약 16.6 GB | 256 MB | y ≥ 32 | 524,288개 — 넘침 |
| 16384 | 1,048,576 | 약 66.6 GB | 1 GB | y ≥ 8 | 2,097,152개 — 넘침 |

- `narew.brick.write_flat_world`가 region 레코드 하나를 반복해 큰 평지 월드를 바로 파일로 쓴다(시험할 때 16384도 1초 안팎,
  348MB). tail은 `map\256_flat.bwr`(개인 서버 템플릿, `server` 배치) 것을 쓰고 시작 위치만 가운데로 옮긴다.
  tail 길이는 64~1024 월드에서 358~454바이트로 크기와 상관없다.
- 클라이언트는 `Local\*.*`를 훑어 `.bwr` 파일로 월드 목록을 만든다(`LocalPlay\ConnectUI.cpp`). 그래서 새 이름의
  `.bwr`만 넣으면 목록에 뜬다(추론).
- **인게임 확인 (2026-10-14):** 평지 2048·4096·8192·16384를 `Local\t<크기>.bwr`로 넣어 시험했다. 사용자 확인 —
  **4096까지 잘 된다.** 게임이 `t4096`을 4096×4096 그대로 다시 저장했고(22.8 MB, `backup_map`에도 한 벌),
  `.lnd`를 땅 131,072칸(magic `0x850490`, 8 + 131,072 × 132 = 17,301,512바이트)으로 새로 썼으며, y=1~250 금 기둥(y=128 위 포함)도
  그대로 남았다. `.bwr`만 넣어도 게임이 `.db`/`.lnd`/`.npc`를 만들어 준다. 8192·16384는 게임이 저장하지 않았다.
  그래서 `world_new`와 "게임에 넣기"는 **4096×4096까지만** 허용한다(`narew.brick.WORLD_SIDE_MAX`).

## 4. 브릭 ID ↔ 종류 (`XML/brick_table.xml`, 173종)

| 범위 | 종류 |
|---|---|
| 1–4 | 풀흙, 흙, 모래, 암석 |
| 5–14 | 광석(구리·주석·철·석탄·퇴적암·대리석·은·금), 눈(미사용), 얼음 |
| 15–19 | 잡초, 꽃 4종 (높이 5 = 반 칸) |
| 20–33 | 금속 블록, 벽돌, 유리 7색 |
| 34–39 / 40–45 / 46–51 | 나무 뿌리 / 줄기 / 잎 (과실·소나무·참나무·정글·아카시아·선인장 순) |
| 52–59 | 통나무, MDF, 합판 |
| 60 | unknown — 맵 바닥 y=0 층 (관찰: 표본 region 1,600개 중 1,571개가 y=0 층 256칸 전부 60, 나머지는 물 240 포함) |
| 61–66 | 묘목 6종 |
| 67–159 | 장식 블록(색 블록, 가공 목재, 단청, 모자이크, 대리석, 철판, 반 블록 127–133·141–148, 틴트 유리 152–159) |
| 240–249 | 물 10단계 (Water10 = 가득 → Water1) |
| 250–254 | 눈 1–5단계 |
| 255 | 충돌 채움 (표에 없음, 1장 참고) |

- `geometry height`: 10 = 한 칸, 5 = 반 칸, 1–9 = 물/눈 단계. `next`는 다음 단계 ID(물 흐름·나무 종류 연결).
- `color`가 `0xFFFFFFFF`인 브릭은 텍스처로 색이 정해진다. `narew`는 팔레트를 만들 때 `item_table.xml`(type="Brick",
  `<model>`=brick id)의 아이콘 평균색을 대신 쓴다.
- **미해결:** 프리팹 일부(`tree_01_04m…40m`, `Choga_*`, `block_*`, `character_select`)가 표에 없는 ID 171, 177, 183, 184, 191을
  쓴다. 현재 ID + 137과 정확히 대응한다(171→34 뿌리, 177→40 줄기, 183→46 잎, 184→47 잎, 191→54 참나무 통나무) — **추론**:
  옛 브릭 표 기준으로 저장된 프리팹. 엔진의 `CBrickTable`에는 재매핑 코드가 없으므로 게임에서는 정의되지 않은 브릭으로 보일 것이다(미확인).

## 5. 압축과 재현성

- `.iaf`는 zlib 레벨 6(`78 9C`), `.bwr`은 대부분 레벨 1(`78 01`)이고 `normal_map.bwr`만 레벨 6이다.
- Python `zlib`가 같은 레벨로 출고본의 모든 스트림을 그대로 재현한다 (관찰: 56 + 29 파일 전수).
  `narew`는 스트림 헤더의 FLEVEL 비트로 레벨을 골라 재압축하고, 수정하지 않은 스트림은 그대로 복사한다.
- 엔진은 `Nw::IZlib`(NarewEngine2)로 압축·해제한다. region 로더는 크기와 상관없이 항상 inflate하므로 브릭 스트림은 반드시 zlib이어야 한다 (관찰).

## 6. `narew brick` 명령

```bash
python -m narew brick info D:\isletre\extracted\map\map\64_0.bwr
```
```bash
python -m narew brick verify D:\isletre\extracted\architecture D:\isletre\extracted\map --recompress
```
```bash
python -m narew brick export D:\isletre\extracted\architecture\Architecture\building\Choga_big_A01.iaf -o choga.vox
```
```bash
python -m narew brick export D:\isletre\extracted\map\map\normal_map.bwr -o normal_top.png
```
```bash
python -m narew brick import mybuilding.vox -o mybuilding.iaf
```
```bash
python -m narew brick paste D:\work\64_0.bwr mybuilding.iaf --at 24,110,24 -o D:\work\64_0_mod.bwr
```

- `export`: 확장자로 형식 결정 — `.vox`(MagicaVoxel, 팔레트 번호 = 브릭 ID), `.png`(`--view top|front|side` 미리보기),
  `.json`(크기·오브젝트·tail 정보, `.iaf`는 브릭 목록 포함, `.bwr`은 `--bricks`로 포함). `--box x0,z0,x1,z1`, `--y y0,y1`로 자르기.
  256을 넘는 영역은 256³ 모델 여러 개 + 장면 그래프로 나눠 쓰고, 큰 맵은 자동으로 `--surface`(공기와 닿은 브릭만)로 줄인다
  (`normal_map.bwr` 전체: 550만 복셀, 모델 16개, 약 8초). 충돌 브릭(255)은 `--keep-collision`이 없으면 뺀다.
- `import`: `.vox` → `.iaf` (vox 팔레트 번호를 브릭 ID로 사용, `--match-colors`면 가장 가까운 브릭 색으로 변환).
- `paste`: `.iaf`/`.vox`를 `.bwr`의 지정 위치에 찍는다. 오브젝트·tail은 그대로 두고 바뀐 region만 재압축한다.
  `-o` 없이 쓰면 제자리 수정 + 처음 한 번 `.orig` 백업. 기존 설치물·나무 위에 덮어써도 오브젝트는 지우지 않으니 주의.
- 오브젝트 extra를 해석하려면 `product.xml`이 필요하다. 추출 폴더 구조(`…\extracted\xml\XML`)를 자동으로 찾고,
  다른 위치면 `--xml`로 지정한다. 없거나 모르는 데이터가 나오면 그 region의 오브젝트 블록을 바이트 그대로 보존한다
  (마지막 region은 tail 위치를 알아야 하므로 실패 시 오류).
- 라이브러리: `narew.brick.Architecture / World / Region / WorldObject`, `narew.voxel.write_vox / render_top`.

## 7. 엔진 근거 요약

| 함수 | 주소 | 확인 내용 | Evidence |
|---|---|---|---|
| `CBrickArchitecture::Load/Save/Create` | `0x1800a0130` / `0x1800a0340` / `0x18009fd90` | `.iaf` 전체 레이아웃, 그룹 순서 | ev_f769a54d |
| `IBrickGroup::GetLocalBrick/SetLocalBrick/CreateData`, `CBrickMemoryIO::Get/SetBrick` | `0x1800a8060` 외 | 1바이트 브릭, 인덱스 식, 높이맵 | ev_599a30c0 |
| `IBrickServer::LoadRegions/SaveTo/SaveHeader/SaveTail` | `0x1800451a0` 외 | `.bwr` 헤더·오프셋 표·tail | ev_d5656987 |
| `IBrickServer::CreateRegions/LoadBalls` | `0x1800459c0` / `0x1800457e0` | region 순서 (i%W16, i/W16) | ev_8472c082 |
| `IBrickServerRegion::LoadFromFile/SaveToFile` | `0x180094650` / `0x180094f10` | region 레코드 | ev_0e6bcd09 |
| `IWorldObject::SaveBinary/LoadBinary` + getter | `0x1801ce000` / `0x1801ce0e0` | 오브젝트 머리 | ev_a05df9d3, ev_802843e9 |
| `IProductType::SeekExtraBin` | `0x18018ca80` | extra 크기 규칙 | ev_060d2f0d |
| `IProductType::ParsingType`, 이벤트 이름 표 | `0x18018bf90`, `0x180398d90` | event/option 해석 | ev_a12fadfa, ev_ba910948 |
| `CProductManagerServer::CreateObject` | `0x1801993e0` | event → 클래스 | ev_aa9004db |
| 클래스별 `Save/LoadExtraBin`, `IItem::LoadBin/SaveBin` | 여러 곳 | 규칙 교차 확인 | ev_b7fdc06b, ev_a75e56cb, ev_5608c06c, ev_21b90f86 |
| `IWorldFile::Create`, `IWorldFileRegion::Create` | `0x1800606d0` / `0x180060330` | 클라이언트 측 `.bwr` 읽기, `mapfile` tail | ev_392ca926 |
| `CBrickTable::GetType/ParsingBrick` | `0x1800af7f0` / `0x1800af900` | 브릭 ID 직접 인덱스 | ev_fa860475 |

## 8. 검증 결과 (2026-10-13)

- `.iaf` 56개: 디코드 → 인코드 바이트 동일, 그룹 전부 강제 재압축해도 동일.
- `.bwr` 29개 (1024×1024 맵 7개 포함): 디코드 → 인코드 바이트 동일, region 전부 강제 재압축해도 동일.
  오브젝트 레코드 분해 실패 region 0개. `python -m narew brick verify … --recompress`로 재현 가능.
- `.vox` 내보내기: 다시 읽은 복셀 집합이 원본 브릭과 정확히 같음 (`Choga_big_A01.iaf` 4,902개, `64_0.bwr` 422,608개).
  `vox → iaf` 가져오기도 원본 브릭과 동일. `paste` 후 오브젝트·tail 보존 확인.
- 미리보기 PNG로 방향 확인: `logotext01.iaf` 정면 = "ISLET", `normal_map.bwr` 위에서 본 지형·호수·나무가 자연스러움.

## 9. 미해결 / 검증 필요

1. **인게임 확인 안 함**: 수정한 `.bwr`/`.iaf`를 게임이 읽는지 (맵은 `map.pack`·`scene.pack`·`architecture.pack` 안에 있으므로
   `narew patch`로 교체하거나 낱개 파일 우선 규칙(01장 5절)을 이용).
2. ID 171–191 프리팹 브릭의 의미 (4장).
3. tail의 `0x771231` 목록(44바이트 레코드), 땅 소유권 데이터, `0xFC8818C8` 블록, `mapfile` 배치의 a·b 값 의미.
4. special 목록 4번째 바이트의 정확한 용도 (추론: 갱신 대기 브릭).
5. `IItemExtra`(책·서명·NPC 아이템) 가변 데이터 — 출고본에는 없음.
6. 서버의 `LoadRegions`는 `mapfile` 배치 tail을 그대로 읽으면 필드가 어긋난다. 이 템플릿들을 서버가 어떤 경로로 여는지는 미확인
   (클라이언트 `IWorldFile`은 정상적으로 읽음).
