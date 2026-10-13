# NarewEngine2 / Islet Online 구조 개요

> 관찰(observed) = 바이너리에서 직접 확인, 추론(inferred) = 근거 기반 추정. 구분해서 표기한다.

## 1. 3계층 구조 (관찰)

| 계층 | 파일 (x64) | 규모 | 역할 |
|---|---|---|---|
| 엔진 | `NarewEngine2_Release64.dll` | 함수 18,270 / export 7,050 / RTTI 클래스 407 / 네임스페이스 `Nw` | 렌더링, 리소스, GUI, 사운드, 물리, 네트워크, 파일 시스템 |
| 게임 | `IsletEngine_release(x64).dll` | export 13,803 / RTTI 클래스 561 / 네임스페이스 `Islet` | 게임 로직 **클라이언트 + 로컬 서버** |
| 실행 파일 | `IsletClient_release(x64).exe` | export 0, import 2,388 (Narew 551 + Islet 1,732) | 얇은 호스트 |

32비트 빌드(`*_release.exe`, `NarewEngine2_Release.dll`, `IsletEngine_release.dll`)도 함께 배포된다.

**핵심:** 두 DLL 모두 C++ 심볼을 이름 그대로 export 한다 (`?LoadPackage@IFilePackage@Nw@@…`).
`re/*.exports.demangled.txt`에 전체 API 시그니처가 있고, `re/*.vtables.json`에 모든 클래스의
가상 함수 테이블과 상속 관계가 있다. 엔진 재구현(drop-in 교체 DLL)의 뼈대로 그대로 쓸 수 있다.

## 2. 사용 기술 스택 (관찰: import / 내장 심볼)

| 영역 | 구현 |
|---|---|
| 렌더러 | Direct3D 9 (`d3d9.dll`, `d3dx9_43.dll`), HLSL `.fx` 이펙트 (shader.pack에 소스 포함, GLES2 경로 흔적 있음) |
| 물리 | NVIDIA PhysX 3 (`PhysX3_x64.dll`, `PhysX3Common_x64.dll`) |
| 사운드 | FMOD (`fmod64.dll`) |
| UI | 자체 GUI (`IGUI*` 약 60개 클래스, XML 레이아웃) + Chromium Embedded (`libcef.dll`, `CHTMLEngine`) |
| 플랫폼 | Steam (`steam_api64.dll`, 암호화 앱 티켓) |
| 내장 라이브러리 | zlib, QuickLZ, libpng, TinyXML, CxImage, MD5/SHA1/HMAC |
| 툴체인 | MSVC 2010 런타임 (`MSVCR100.dll`, `MSVCP100.dll`) |
| 메모리 | 커스텀 할당자 (`IMemoryManager`, 전역 `0x180750ae8`, 클래스명 태그 포함) |

## 3. 주요 엔진 서브시스템 (export 클래스 기준)

- **리소스/파일**: `IFileSystem`, `IFilePackage`, `IFilePackageMaker`, `IFileSeeker`, `IFileResourceManager`, `IMD5HashTable`
- **렌더링**: `IRenderDevice`(82 메서드), `IShader*`, `ITexture*`, `IVertexBuffer`, `IMaterial`, `IMesh`, `ISkinMesh`, `IModel`(91), `ILight*`, `IPostEffect`, `CShadowPSM`, `ISkyDome`, `CGrassManager`
- **애니메이션**: `IAnimation*`, `IBone`, `ISpecialBone`, `IAnimationExtraData`
- **GUI**: `IGUICore`(95), `IGUINode`(110), `IGUIParser`(54), 버튼/이미지/스크롤/드래그 등
- **파티클/이펙트**: `IParticle*`, `ITrailEffect`, `ILensFlare`
- **네트워크**: `INetworkCore`, `INetworkSession`(+UDP), `IPacketReader/Writer`, `IHttpDownloader/Uploader`
- **물리**: `IPhysicsDevice`, `IPhysicsActor`, `IPhysicsRope`, `IPhysicsBalloon`

## 4. 게임 계층 특징 (관찰)

- 복셀/블록 월드: `IBrickWorld`, `IBrickGroup`, `CBrickWorld`, `IBrickWorldGenerator`, `CTerrainWorld`
- **로컬 서버 내장**: `CLocalServer`, `CLocalServerApp`, `CServerCharacter`, `CServerNpc`, `CProduct*Server`(약 70종), `CPrivateServerType` → 싱글/개인 서버 로직이 클라이언트 DLL 안에 있음. 게임플레이 모딩을 공식 서버와 무관하게 로컬에서 할 수 있는 구조 (추론)
- 데이터 테이블: `CItemTable`, `CCraftingTable`, `CDropTable`, `CNpcType`, `CBuffTable` … (xml.pack의 XML과 연결될 것으로 추론)
- 로직 회로: `CProductAndGateServer`, `Or/Xor/Nand/Nor/Not`, 스위치, 압력판 등

## 5. 에셋 포맷 현황 (16,093 파일)

| 확장자 | 개수 | 형식 | 상태 |
|---|---|---|---|
| png/jpg/tga/bmp | 10,002 | 표준 이미지 | 바로 편집 가능 |
| wav/ogg/mp3 | 246 | 표준 오디오 | 바로 편집 가능 |
| ttf/otf | 11 | 폰트 | 바로 편집 가능 |
| `.nwmt` | 3,633 | **XML** — 3ds Max 익스포터 출력: 재질(셰이더/텍스처), 메시, 정점, 변환행렬 | 텍스트 편집 가능, glTF 변환기 필요 |
| `.nwat` | 1,373 | **XML** — 메시/본별 pos/rot/scale 키프레임 (EUC-KR) | 텍스트 |
| `.nwbt` | 81 | **XML** — 스켈레톤(본 계층 + 본 메시) | 텍스트 |
| `.xml` | 447 | GUI 레이아웃, 이펙트, 게임 테이블 | 텍스트 |
| `.fx/.vs/.ps/.fxh/.hlsl/.shader` | 180 | HLSL 소스 | 텍스트 |
| `.effect` / `.scene` | 4 / 4 | XML (파티클 그룹 / 씬) | 텍스트 |
| `.iaf` | 56 | 바이너리: `u32 0x203`, `16×16×16`, u32, u32 압축크기, zlib | 블록 건축 프리팹으로 추정 |
| `.bwr` | 29 | 바이너리: `u32 0x870203`, `128×128×64`, …, zlib | 블록 월드 영역으로 추정 |
| `.character` | 1 | 바이너리, 매직 `0xABCDEFAB` (세이브 `Local\character.db`와 동일) | 미해석 |
| `.db` | 4 | Windows `Thumbs.db` (실수로 포함된 파일) | 무시 |

`.nwmt`의 `ticksperframe="160"`, `"Scene Root"`, 원본 경로 `:\morenori\2. Graphic\…` 로 보아 3ds Max 플러그인 출력이다.
