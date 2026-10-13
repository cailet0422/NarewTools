# .pack 아카이브 포맷 (NarewEngine2) — 확정 사양

> 상태: **완전 해석 완료.** 출고된 20개 팩 전체(16,093 파일)를 추출·MD5 검증했고,
> 추출본에서 다시 빌드한 팩 20개가 **원본과 바이트 단위로 동일**함을 확인했다.
> 근거: `NarewEngine2_Release64.dll` (SHA-256 `40d2a2fc…b23c`) Ghidra 디컴파일,
> REA evidence 번들 `re/rea-evidence-session1.json`.

## 1. 전체 구조

```
+--------------------------+  offset 0
| 데이터 영역              |  엔트리마다 [u32 size][size 바이트]
|  (디렉터리 순서와 동일)  |  size 바이트 = zlib 스트림 또는 원본 그대로
+--------------------------+  dir_offset (= data_end)
| 디렉터리                 |  count 개의 엔트리 레코드 (가변 길이)
+--------------------------+  file_size - 40
| 트레일러 (40 바이트)     |
+--------------------------+  file_size
```

암호화 없음. 모든 정수는 리틀엔디언.

## 2. 트레일러 (파일 끝 40바이트)

| 오프셋 | 타입 | 이름 | 의미 |
|---|---|---|---|
| +0x00 | u64 | file_size | 팩 파일 전체 크기 |
| +0x08 | u64 | dir_offset | 디렉터리 시작 위치 (로더가 여기로 seek) |
| +0x10 | u64 | data_end | 데이터 영역 끝 (= dir_offset) |
| +0x18 | u64 | payload_bytes | 4바이트 size 접두사를 뺀 페이로드 합계 (= data_end − 4·count) |
| +0x20 | u32 | count | 엔트리 수 |
| +0x24 | u32 | magic | `0x87020200` (바이트열 `00 02 02 87`) |

빈 팩(`voice.pack`)은 트레일러 40바이트만으로 이루어진다.

## 3. 디렉터리 엔트리

| 필드 | 타입 | 설명 |
|---|---|---|
| attribute | u8 | 빌드 시 `Nw::FILE_ATTRIBUTE`. 0=기본, 1=이미지 축소 금지, 2/3/4=이미지를 1/2/3 밉 단계 축소. **로드 시에는 무시됨** |
| compressed | u8 | 1 = zlib, 0 = 원본 저장 |
| reserved0 | u8 | 항상 0 |
| reserved1 | u8 | 항상 0 |
| raw_size | u32 | 압축 해제 크기 |
| packed_size | u32 | 압축 크기 (저장 엔트리는 0) |
| offset | u64 | 이 엔트리의 `[u32 size]` 접두사 위치 |
| md5 | u8[16] | **압축 해제된** 데이터의 MD5 |
| name_len | u16 | 이름 길이 |
| name | char[name_len] | `\` 구분자, 원래 대소문자 유지, ANSI (출고본은 전부 ASCII) |

## 4. 엔진 동작 (디컴파일 근거)

| 동작 | 주소 | 관찰 내용 |
|---|---|---|
| 팩 열기 | `0x1801566a0` | 마지막 0x28 바이트를 읽고 magic `0x87020200` 확인 |
| 디렉터리 파싱 | `0x1801573a0` | u8×4, u32, u32, u64, 16B, u16, name 순서로 읽음. 이름의 A–Z만 소문자로 바꿔 인덱싱. **중복 이름이 있으면 팩 로드 실패** |
| 파일 읽기 | `0x180157c10` | offset으로 seek → u32 size 읽기. compressed=0이면 size==raw_size 요구, 1이면 size==packed_size 요구 후 inflate. **MD5 검증 안 함** |
| 팩 저장 | `0x1801568d0` | "Packing :: Write Files / File List / Header" 순서 |
| 파일 기록 | `0x180156c80` | zlib 압축 결과가 원본보다 작을 때만 압축 저장, 아니면 원본 저장. MD5는 원본 데이터로 계산 |
| 압축 레벨 | — | Python `zlib.compress(data, 6)`이 출고된 압축 스트림을 그대로 재현 (전 팩 검증) |
| 팩 빌드 규칙 | `0x1801421e0` | 원본 에셋 루트의 각 최상위 폴더 → `data\<폴더>.pack`, 루트의 낱개 파일 → `data\default.pack` |
| file.hash | `0x1801426c0` | `CFilePackageMaker`(빌드/패치 도구)만 생성·비교. 게임 DLL·클라이언트에는 참조 없음. 인게임 테스트에서 `file.hash`와 MD5가 다른 패치된 `gui_skin.pack`으로도 게임이 정상 실행됨 → 실행 시 무결성 검사로 막지 않음 (관찰) |

## 5. 파일 시스템 조회 순서 (`Nw::CFileSystem`, vtable `0x180681758`)

| 슬롯 | 주소 | 역할 |
|---|---|---|
| 5 | `0x180159810` | OpenFile(name): 모드에 따라 아래 두 순서 중 하나, 실패 시 보조 파일시스템 |
| 8 | `0x180159e70` | AddPackage(path) — 같은 이름 팩이 이미 있으면 무시 |
| 11 | `0x180159570` | **SetDiskFirst** (mode=1): 디스크의 낱개 파일 → 팩 순서 |
| 12 | `0x180159580` | **SetPackFirst** (mode=0, 기본값): 팩 → 디스크 순서 |

- 엔진 초기화(`0x18016e200`)는 `IFileSystem::CreateDefault()`(팩 우선)를 설치한다.
- 팩 우선 모드에서도 **팩에 없는 경로는 디스크에서 읽는다** → 새 파일 추가는 낱개 파일로 가능.
- **인게임 관찰 (2026-10-13):** `gui_skin.pack`을 원본(`.orig`와 바이트 동일)으로 둔 채
  `<게임 폴더>\gui_skin\19_Login_Create\morenori.png`, `morenori_width.png`를 낱개 파일로 두자
  인트로에 **낱개 파일의 로고가 표시됨** → GUI 텍스처는 디스크의 낱개 파일이 팩보다 우선한다.
- 원인은 미확정 (추론 후보): 게임 코드가 SetDiskFirst를 호출하거나, GUI 텍스처 로더가 파일
  시스템을 거치기 전에 디스크를 먼저 확인한다. 모델·XML·사운드 등 다른 리소스에도 적용되는지는
  미확인 (REA unknown `unk_66f68ceb…`, 부분 해결).

## 6. 미해결 / 검증 필요

- [x] 패치한 팩을 실제 게임이 정상 로드하는지 인게임 확인 (REA unknown `unk_7eb6bd57…`) — **확인됨 2026-10-13**:
  `gui_skin\19_Login_Create\morenori.png`, `morenori_width.png`를 `narew patch`로 교체 → 인트로 로고가 바뀐 이미지로 표시됨 (사용자 PC 관찰)
- [x] 낱개 파일이 팩 안 파일을 덮어쓰는지 — GUI 텍스처는 **예** (2026-10-13 인게임 확인)
- [ ] 같은 동작이 모델(.nwmt)·XML 테이블·사운드에도 적용되는지, 그리고 그 원인(SetDiskFirst 여부)
- [ ] 32비트 `NarewEngine2_Release.dll`이 동일 포맷인지 (같은 팩을 공유하므로 사실상 확실)
