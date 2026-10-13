# 모델·스켈레톤·애니메이션 포맷 (.nwmt / .nwbt / .nwat) 과 glTF 변환

> **관찰(observed)** = 엔진 바이너리(`NarewEngine2_Release64.dll`, SHA-256 `40d2a2fc…adb23c`)의
> 디컴파일 결과 또는 전체 데이터 전수 조사로 직접 확인한 것. **추론(inferred)** = 근거는 있지만 실행으로
> 확인하지 않은 것. 둘을 구분해서 적는다.
>
> - 엔진 근거: REA(Ghidra 12.1.4) Evidence 21건 → `re/rea-evidence-models.json` (아래 `ev_xxxxxxxx…`는 앞 12자리)
> - 데이터 근거: 5,087개 파일 전수 조사 → `re/model-survey.json` (`tools/survey_models.py`로 재생성)
> - 수치 검증: `tools/roundtrip_models.py`, `tools/roundtrip_anims.py`, `tools/validate_gltf.py`

## 0. 한눈에 보기

| 항목 | 규칙 | 근거 |
|---|---|---|
| 좌표계 | **왼손 좌표계, +Y 위**, 캐릭터는 **−Z를 바라봄**, 왼쪽이 +X | 관찰: `Matrix4::SetProjection`(0x180169c10)=PerspectiveFovLH, `SetView`(0x1801697f0)=LookAtLH (`ev_169501b6dbeb`). RH 버전은 별도 `*RH` 함수. 데이터: 말 머리 z=−0.8, 발끝이 −Z |
| 행렬 | 행 우선(row-major) 4×4, **행 벡터** (`v' = v·M`), 이동은 3행 | 관찰: `ParsingMatrix`(0x1801319e0)가 `row` 속성을 행으로 저장, `Matrix4Multiple(out,A,B)`=A×B (0x180168c00), 셰이더 `mul(float4(p,1), g_matWVP)` |
| 쿼터니언 | `x y z w` (w 마지막), D3DX 방식 회전행렬 | 관찰: `Quaternion::GetMatrix4`(0x18016b640) `m[0][1]=2(xy+wz)`, 단위 쿼터니언 기본값 `(0,0,0,1)` (`ev_01fee8528dc6`) |
| 로컬 변환 | `M = S · R · T` | 관찰: `Matrix4::SetWorld`(0x180169ed0, `ev_7c3b8263b937`) |
| 계층 | `world = local × parentWorld`, 스킨 행렬 = `inverse(bindWorld) × world` | 관찰: `IAnimationCtrl::ApplyHierarchyBone`(0x180067fc0, `ev_4d2b26bcd3cd`) |
| 바인드 포즈 | 본의 `<transform>` = 바인드 포즈 **월드** 행렬, 엔진이 역행렬도 저장 | 관찰: 0x1800a7850에서 `ParsingMatrix` 직후 `Matrix4x4_InverseGLU` (`ev_ebe61bea07c7`) |
| `init_key` | 부모 기준 로컬 pos/rot(xyzw)/scale | 데이터: `SetWorld(init_key)×parentWorld == <transform>` 1,517/1,518 본 (오차 p99 1.9e-4) |
| 스킨 | 정점당 `<skin>` 4개, 본 인덱스 = **전역 본 번호**, 가중치는 앞 3개만 엔진이 읽음 | 관찰: 0x1800a99a0 (`ev_1bdecf8faa65`) |
| 키 ↔ 시간 | 키 `n` = 프레임 번호, 마지막 키 이후는 **마지막 키 유지** | 관찰: `IAnimationKey::GetPos`(0x180065420, `ev_ccbb63f7c862`) |
| 보간 | 위치·스케일 선형, 회전 slerp(최단 경로) | 관찰: `BlendPosKey`/`BlendRotKey`, `Quaternion::Slerp`(0x18016b950) |
| 재생 속도 | 헤더 `framerate` 값을 **키 하나당 밀리초**로 사용 (30 → 30ms/키 ≈ 33.3fps) | 관찰: 0x1800a5f10 → `GetTicksPerFrame` 필드, `CAnimationPlayer` 업데이트(0x18006dc50, `ev_205794ca147f`) |
| 텍스처 경로 | `''` 또는 `:`로 시작하면 무시, 그 외 `모델폴더 + 이름` | 관찰: `IMaterial::LoadTexture`(0x180163750, `ev_71a42b31be10`) |

## 1. 공통 — XML 파일 형식 (관찰)

- 3ds Max 익스포터가 쓴 XML. **루트가 여러 개**(`<header>`, `<material_list>`, …가 최상위에 나란히)라 표준 XML이 아니다.
  엔진은 TinyXML 계열(`IXMLParser::LoadRapid`)로 읽는다. 변환기는 가상의 루트로 감싸서 읽는다(`narew/nwxml.py`).
- 인코딩 **EUC-KR(cp949)**, 선언에 encoding 없음(.nwat 217개만 `encoding="euc-kr"`), 줄끝 **CRLF**,
  들여쓰기 4칸(일부 파일 2칸·탭 혼용).
- 숫자 형식: 정점·법선·UV·탄젠트 `%.5f`, 행렬 `%.6f`, `bound_box`·가중치 `%g`
  (지수 표기는 739개 파일이 2자리 `e-05`, 244개가 MSVC 구형 3자리 `e-005`).
- 엔진(TinyXML)은 넘어가지만 표준 XML 파서는 실패하는 결함 2건:
  `m1_look_mummy_002.nwmt`(텍스처 경로 안의 `&#x1D;`), `wand_11.nwmt`(`z="0.02"link_bone=` 속성 사이 공백 없음).
  변환기는 파싱 실패 시에만 이 두 가지를 고쳐서 다시 읽는다.
- 전수 조사 결과 5,087개 파일 모두 파싱됨 (.nwmt 3,633 / .nwbt 81 / .nwat 1,373).

## 2. .nwmt — 모델

### 2.1 최상위 섹션 (관찰: 모델 로더 `FUN_1800a8f00`, `ev_4edec99c0c1a`)

| 섹션 | 개수(파일) | 엔진 처리 |
|---|---|---|
| `header` | 3,633 | 찾기만 하고 **값은 읽지 않음** (개수 불일치 파일 250여 개도 정상 동작하는 이유) |
| `material_list` | 3,633 | `FUN_1800a9340` → `IMaterial::LoadFromXML` |
| `mesh_list` / `skinmesh_list` | 1,286 / 2,337 | 자식 `<mesh>`/`<skinmesh>`를 순회 (`count` 속성은 무시) |
| `special_bone_list` | 3,633 (내용 있는 것 433) | 물리 본(머리카락·치마 등) → `ISpecialBone` |
| `physics_list` | 433 | PhysX 액터·조인트 |
| `light_list`, `helper_list` | 162, 549 | 조명, 부착점(`Point_Hand_L` 등) |
| `tag_list`/`sound_list`/`effect_list` | 일부 | 애니메이션 부가 데이터와 같은 파서 |
| 최상위 `bound_box` | 3,583 | **로더가 읽지 않음** (메시별 bound_box로 다시 계산) |

### 2.2 material (관찰: `IMaterial::LoadFromXML` 0x180163d00)

```xml
<material id="0" name="16 - Default">
    <shader file="DefaultSkinMesh.fx" technique="RenderDefault" />   <!-- 없으면 defaultmesh.fx -->
    <tile row="2" col="4" frame="0" />                                <!-- 스프라이트 시트 -->
    <color ambient="0xff808080" diffuse="0xffffffff" specular="0xffffffff" result="0xffffffff" />
    <specular power="1" gloss="10" />
    <diffuse_map>f1_body01.png</diffuse_map>
    <normal_map></normal_map>
    <specular_map>f1_body01_specular.tga</specular_map>
    <mask_map>f1_body01_mask.png</mask_map>
    <param_list count="3"> <param name="g_cMaskRed" value="0xff…"/> … </param_list>  <!-- 선택 -->
</material>
```

- 색은 `0xAARRGGBB` (`SColor8::SetHex`). `result` 색은 로더가 읽지 않는다.
- `tile`: `row`·`col`을 1..128로 자른 뒤 UV 스케일 `(1/row, 1/col)`, 프레임 오프셋
  `((frame % row)/row, (frame / row)/col)` (관찰: `IMaterial::SetTile` 0x180163440/0x180163470, `ev_63c7f162a291`).
  즉 `row`는 **가로** 칸 수다. 얼굴의 눈/입 표정 아틀라스(2×4)가 이것으로 한 칸만 보인다.
- 텍스처 경로: 비었거나 `:`로 시작하면(`:\morenori\2. Graphic\…` 같은 원작자 PC 경로) **로드하지 않음**,
  아니면 `모델 폴더 + 이름`. 전수 조사: diffuse_map 4,009개 해석됨 / 142개 파일 없음.
  normal_map의 대부분(2,256개)은 무시되는 절대 경로다.
- `param_list`(마스크 색 `g_cMaskRed/Green/Blue`)는 로드 플래그 4일 때 `IMaterialMask`가 읽는다.
  게임의 diffuse 텍스처 대부분이 회색조이고 mask_map 영역에 이 색을 곱해 염색한다(추론: 셰이더 이름·데이터).
- 셰이더 16종 (`DefaultSkinMesh.fx`, `DefaultMesh.fx`, `product.fx`, `DefaultSky.fx`, `…_ghost.fx` 등).

### 2.3 mesh / skinmesh (관찰: `FUN_1800a95c0`, `FUN_1800aa300`, 정점 `FUN_1800a99a0`)

```xml
<skinmesh id="0" name="f1_body001" use_bone="27" material="0">   <!-- mesh에는 use_bone 없음 -->
    <parent id="-1" name="Scene Root" />
    <transform> <tm row="0" value="1 0 0 0"/> … </transform>
    <bound_box minx=… maxz=… />
    <vertex count="476">
        <point_list>   <point n="0" value="x y z" />   …
        <skin_list>    <skin vert="0" n="0" bone="14" weight="0.56" />  (정점당 n=0..3)
        <normal_list>  <normal n="0" value="x y z" />
        <tangent_list> <tangent n="0" value="x y z w" />
        <uv_list>      <uv n="0" value="u v" />
    </vertex>
    <index count="626" b32bit="0">  <face n="0" a="173" b="174" c="171" />  </index>
</skinmesh>
```

| 요소 | 엔진 동작 (관찰) | 데이터 (관찰) |
|---|---|---|
| 정점 형식 | 위치(마스크 1), 법선(0x40), 탄젠트 xyzw(0x80, **로드 플래그 2일 때만**), 블렌드 인덱스 4바이트(4), 블렌드 가중치 float×3(8), UV0(0x200) | 정적 2,042개 중 2,030개가 탄젠트 있음, 스킨 2,723개 전부 |
| `binormal` | 읽지 않음. 셰이더가 `cross(N,T)·T.w`로 계산 | 파일에도 없음 |
| `n` 속성 | 배열 인덱스로 사용 | 전부 0..count-1 순서 |
| `uv` | `sscanf("%f %f")` | **44개 파일**은 `"0.24119 "`처럼 v가 없음(익스포터 버그) → 엔진에서는 v 미설정 |
| `<index>` | `(a,b,c)`를 그대로 16비트 인덱스로. `b32bit=1`이면 32비트, `twopass>0`이면 `(a,c,b)` 사본을 추가(양면) | 전부 16비트, twopass 없음 |
| `transform` | `ISceneNode::SetLocalTM`(+8 위치, `ev_ecdda52419c3`) — **모델 공간** 행렬 | 정적 메시: 점은 메시 로컬, `bound_box`=로컬 점의 bbox(표본 600개 파일의 정적 메시 325개 모두 일치). 행렬식 전부 양수(미러 없음) |
| `parent` | `id ≥ 0`이면 `SetParentID` | 175개 메시가 부모 있음. 부모가 있어도 `transform`은 모델 공간(애니메이션 키 0 × 부모 = transform, 26건 확인) |
| skinmesh `transform` | 읽지만 스키닝에 쓰이지 않음(추론) | 9개만 단위행렬이 아님. 정점은 이미 바인드 포즈 모델 공간에 있음(본까지 거리 비교 3/3) |
| `use_bone` | `ISkinMesh::SetUseBoneCount` → `BuildGroup`(본 팔레트 분할) | 2,687개는 실제 쓰는 본 수보다 큼(3ds Max Skin 모디파이어의 본 목록 수로 추정) |

**스킨 가중치 (관찰)**: `bone`은 바이트로 `index[vert*4+n]`에 저장, `weight`는 `n<3`인 것만 float 3개로 저장
→ 4번째 가중치는 `1-합`으로 셰이더가 계산하는 것으로 추론(D3D9 인덱스 블렌딩 관례). 데이터의 가중치 합은
2,721/2,723 메시가 1±0.01, 2개(`wand_tang_01/02`)는 전부 0. 가중치 순서는 정렬되어 있지 않음(1,121개).

**본 번호 (관찰+데이터)**: 스킨 `bone` 값 `b`가 `b < header.bone`이면 스켈레톤(.nwbt)의 `id=b` 본,
아니면 `special_bone_list`의 `id = b − header.bone` 본. 데이터: 특수 본의 `worldid == header.bone + id`가
435/435 파일에서 성립, 특수 본 `parent id`도 이 전역 번호(3,153/3,153). 이름은 중복될 수 있다
(`f1_look_joseon_01`에 `physics_001`이 두 개) → **이름 말고 번호로 연결해야 한다**.

**루트 본 (관찰)**: `ApplyHierarchyBone`에서 부모가 없는 본의 스킨 행렬은 항상 단위행렬로 둔다.
루트(`Bip001`)에 가중치를 준 정점은 루트가 움직여도 따라가지 않는다(glTF와 다른 점, 데이터상 해당 정점 거의 없음).

### 2.4 special_bone / physics / helper (요약)

- `special_bone_list/bone`: `id`, `worldid`, `name`, `parent(id=전역 번호)`, `child count`, `transform`(바인드 월드),
  `init_ani/transform`, 시각화용 `mesh`. 엔진은 스켈레톤 본과 같은 파서(`FUN_1800a7b40`)로 읽는다. `worldid`는 읽지 않는다.
- `physics_list`: PhysX 액터(캡슐/박스 등, 질량·감쇠)와 조인트(스윙/트위스트 한계). 변환기는 손대지 않고 그대로 보존.
- `helper_list/helper`: 이름·타입(`Dummy`, `PointHelper`…), `link`(bone_id 또는 mesh_id + 그 기준 로컬 행렬), 월드 `transform`.

## 3. .nwbt — 스켈레톤 (관찰: `IBone::LoadNw` → `FUN_1800a75b0`/`FUN_1800a7850`)

```xml
<bonelist count="31">
    <bone id="0" name="Bip001">
        <parent id="-1" name="Scene Root" />
        <child_list count="3"> <child id="0" name="Bip001 Footsteps" bone_id="1" /> … </child_list>
        <transform> … </transform>                        <!-- 바인드 포즈 월드 -->
        <init_key pos="0 0.4578 0" rot="0 0.7071 0 0.7071" scale="1 1 1" />   <!-- 부모 기준 로컬 -->
        <init_key2 />
        <mesh vertex="6" face="8"> <point id=…/> <normal id=…/> <face id=… a b c/> </mesh>  <!-- Max 본 모양 -->
    </bone>
```

- `bonelist count`로 본 배열을 만들고 각 `bone id`에 채운다. `transform` → 행렬과 **역행렬**(스킨 바인드) 저장.
  `init_key` → `SetInitKey`(애니메이션이 없을 때의 포즈). `init_ani/transform`이 있으면 별도 저장.
- `init_key`는 소수 4자리라 `transform`과 최대 1.9e-4 차이. 예외 1건(`turkey/rooster.nwbt`의 `ride_point`)은 0.2.
- 81개 파일, 본 수 0~59. F1(여) 31본, M1(남) 29본. 대부분 3ds Max Biped(`Bip001 …`).

## 4. .nwat — 애니메이션 (관찰: `IAnimation::LoadNw` → `FUN_1800a57d0`)

### 4.1 세 가지 변형 (데이터)

| 변형 | 개수 | 특징 |
|---|---|---|
| A. 모델과 같은 헤더 | 1,154 | `<header material= mesh= …><frame ticksperframe framerate start end/>`, 애니메이션에 `name`·`parent` |
| B. `<root>` 래핑 | 218 | `encoding="euc-kr"`, 헤더는 `mesh`·`bone`과 `frame framerate start end`만, `id`만 있음 |
| C. 기타 도구 | 1 | `M1/dance_01.nwat`: 고정밀 실수, `start="1"`, 키 `n`이 1부터 |

엔진은 셋을 구분하지 않는다: `header/frame`의 `framerate`, `start`, `end`만 읽고(`ticksperframe`는 무시),
`mesh_animation_list`·`bone_animation_list`·`material_animation_list`, 그리고 `tag_list`/`sound_list`/`effect_list`.

### 4.2 트랙과 키 (관찰)

```xml
<bone_animation_list count="31">
    <animation id="3">                      <!-- id = 본 번호 (메시 트랙은 메시 id) -->
        <pos_key count="23">   <pos n="0" value="x y z" /> …   </pos_key>
        <rot_key count="23">   <rot n="0" value="x y z w" /> … </rot_key>
        <scale_key count="1">  <scale n="0" value="1 1 1" />   </scale_key>
    </animation>
```

- 키에는 **시간이 없다.** `count` 크기 배열을 만들고 `n`번째 칸에 넣는다(`SetPos(n & 0xffff, …)`).
- 재생: 경과 ms × 속도 = `t`, `key = t / framerate + start`, 다음 키 `key+1`(루프면 프레임 수로 나머지),
  보간 계수 `(t % framerate) / framerate`. 프레임 수 = `end − start`.
  키 인덱스가 키 개수를 넘으면 **마지막 키**(`GetPos`/`BlendPosKey`의 클램프) → 트랙마다 키 수가 달라도 된다.
  변하지 않는 트랙은 키 1개(조사: 위치 트랙 20,967개, 회전 6,915개가 키 1개).
- **재생 속도 주의(관찰)**: 헤더의 `framerate` 값(30)이 `GetTicksPerFrame` 필드(+0x5e)에 저장되고,
  플레이어는 이것을 "키 하나의 밀리초"로 나눈다. 따라서 엔진은 30fps로 만든 동작을 키당 30ms(≈33.3fps)로,
  24fps 동작은 24ms(≈41.7fps)로 재생한다. 경과 시간이 실제 ms라는 것은 추론(호출자 `IAnimationCtrl::Update(ulong)`은
  값을 그대로 전달, `ev_91bcb80a8cc2`).
- 키 개수와 프레임 범위의 관계는 제각각이다: 여러 키 트랙 기준 `end−start−1`개 9,228 / `+1`개 6,931 /
  `end−start`개 1,120 / 기타 1,927. 엔진은 키 번호로만 접근하므로 문제없다.
- 회전 키 길이 0.99999~1.00001(정규화됨). 회전 보간은 내적이 음수면 부호를 뒤집는 최단 경로 slerp.
- 부가 데이터: `tag_list/tag(frame,name="attack"…)`(게임 이벤트), `sound_list/sound(frame,file,loop,radius,volume)`,
  `effect_list/effect(file,frame,link_bone,x,y,z)`, `material_animation_list`(UV 스크롤 키 `u v`).

## 5. glTF 2.0 변환 규칙 (설계)

엔진 공간(왼손, 행 벡터) → glTF(오른손, 열 벡터, +Z가 정면)는 **Z 미러**로 바꾼다. 자기 자신이 역변환이다.

| 대상 | 엔진 → glTF |
|---|---|
| 위치·법선·이동 | `(x, y, z) → (x, y, −z)` |
| 쿼터니언 | `(x, y, z, w) → (−x, −y, z, w)` |
| 행렬 | `M' = S·M·S`, `S = diag(1,1,−1,1)`. 행 벡터 행렬의 16개 값을 행 순서로 쓰면 그대로 glTF 열 우선 배열 |
| 탄젠트 | `(x, y, z, w) → (x, y, −z, −w)` |
| 삼각형 | `(a, b, c) → (a, c, b)` |
| UV | 그대로 (D3D와 glTF 모두 좌상단 원점) |
| 키 시간 | `t = n / framerate` 초 (작업자가 만든 30fps 기준). `--engine-timing`이면 `n × framerate / 1000` 초 |

결과: 3ds Max(Z-up 오른손) → 엔진(Y/Z 교환) → glTF 변환을 합치면 Max `(x,y,z)` → glTF `(x, z, −y)`, 즉 Blender 등이
쓰는 표준 Z-up→Y-up 회전과 같다. 캐릭터는 glTF 규약대로 +Z를 보고 왼쪽이 +X다(Blender 렌더로 확인).

| 엔진 데이터 | glTF |
|---|---|
| 스켈레톤 본 | 노드(TRS = `transform × inverse(부모 transform)` 분해, 즉 rest = bind). `init_key`는 extras에 |
| 특수(물리) 본 | 노드(부모는 전역 번호로 연결), 해당 모델의 skin joints 뒤쪽 |
| skinmesh | mesh + skin. `joints[b]` = 엔진 본 번호 `b` 그대로, `inverseBindMatrices = inverse(transform)` |
| mesh | 노드 TRS(전단 성분이 있으면 matrix), 부모 메시가 있으면 그 자식 |
| material | PBR(metallic 0, roughness 1), `baseColorTexture`=diffuse_map, `normalTexture`=유효한 normal_map, `tile` → `KHR_texture_transform`, 알파 있는 PNG면 `MASK`(추론) |
| helper | 빈 노드(본/메시 링크 아래). 여러 부위 파일에 반복되는 헬퍼는 하나로 |
| .nwat | animation: LINEAR 채널(translation/rotation/scale). 메시 트랙은 같은 이름의 정적 메시 노드에 |
| 나머지 | `extras.narew`에 원본 그대로: 헤더, physics/special_bone/helper/light/effect 섹션 원문, 재질 XML, 키 수, 태그/사운드/이펙트 |

**무손실 장치**: glTF는 법선이 단위 길이, 가중치 합이 1이어야 해서 원본(소수 5자리 법선, 길이 92짜리 법선,
길이 28,000짜리 탄젠트, 합이 1이 아닌 가중치)을 그대로 담을 수 없다. 이 값들은 어떤 primitive도 쓰지 않는
별도 accessor에 넣고 노드 extras에 `[accessor, CRC32]`로 기록한다. `gltf2model`은 CRC가 맞을 때만 쓰므로
다른 도구가 파일을 다시 쓴 뒤에는 표준 속성(NORMAL/TANGENT/WEIGHTS_0)을 쓴다.
(커스텀 정점 속성 `_NAME`을 쓰지 않는 이유: Blender 4.2 가져오기가 여러 개의 커스텀 속성을 `set` 순서로 짝지어
크기가 다르면 오류, 같으면 이름이 뒤바뀐다.)

## 6. 변환기 사용법

```bash
python -m narew model2gltf D:\isletre\extracted\effect\Effect\brick_effect.nwmt -o brick.glb
```
```bash
python -m narew model2gltf f1_body01.nwmt f1_face01.nwmt f1_hair01.nwmt f1_shoes01.nwmt -a D:\isletre\extracted\character\Character\F1\f1_combat_attack_onewp01.nwat -o f1.glb
```
```bash
python -m narew gltf2model f1.glb -o out_folder
```
```bash
python -m narew gltf2anim f1_edit.glb -s f1.nwbt -t f1_combat_attack_onewp01.nwat -o f1_combat_attack_onewp01.nwat
```

- 스켈레톤은 자동으로 찾는다(같은 폴더의 같은 이름 → `f1_`/`m1_` 아이템이면 `character\Character\F1\f1.nwbt` →
  같은 폴더의 .nwbt, 단 본 개수가 모델 헤더의 `bone`과 같은 것만). 다르면 `-s`로 지정.
- 여러 .nwmt를 한 번에 주면 한 캐릭터로 합쳐진다(모델마다 그룹 노드 + skin). `gltf2model`은 그룹마다 원래
  파일 이름으로 .nwmt를 쓰고, 내장 텍스처 중 없는 파일을 같은 폴더에 쓴다(다르면 `--force`일 때만 덮어씀).
- TGA/BMP/PSD 텍스처는 Pillow가 있으면 PNG로 내장한다(없으면 경고 후 생략). `.gltf`로 저장하면 원본을 상대 경로로 참조.

**Blender에서 작업할 때 (확인함, Blender 4.2.3)**
1. 가져오기 전에 **장면 프레임 레이트를 30fps**로 맞춘다(기본 24fps면 애니메이션이 다시 샘플링되고 끝이 잘린다).
2. 내보낼 때 **Include → Custom Properties** 를 켠다(`extras.narew`가 있어야 헤더·물리·재질이 그대로 돌아온다).
3. `gltf2model`(필요하면 `-s 스켈레톤.nwbt`), 애니메이션은 `gltf2anim -t 원본.nwat`(Blender는 애니메이션 extras를
   버리므로 태그·사운드·이펙트·프레임 범위를 원본에서 가져온다).

## 7. 검증 결과

| 검사 | 결과 |
|---|---|
| 전체 .nwmt 3,633개: nwmt → glb → nwmt (메시별 위치·법선·탄젠트·UV·면·스킨·변환·bbox·재질·보존 섹션 수치 비교) | **3,633/3,633 통과**, 그중 **3,529개는 바이트까지 동일**. 나머지는 섹션 사이 빈 줄 등 공백 차이(전체 1,565만 줄 중 3.3만 줄) |
| 위 3,633개의 .glb 구조 검사(`tools/validate_gltf.py`: 인덱스 범위, accessor 범위·min/max, 단위 법선/탄젠트, 가중치 합, joints, 애니메이션 시간) | 오류 0 (경고: skinned mesh 노드가 루트가 아님 — 규격상 허용) |
| 본 애니메이션 .nwat 843개: nwat(+nwbt) → glb → nwat, 엔진 방식으로 모든 프레임 포즈 비교 | **843/843 통과** (위치·스케일 1e-5, 회전 2e-5, q≡−q). 메시 트랙만 있는 499개와 스켈레톤을 못 찾은 31개는 제외 |
| 샘플: `brick_effect.nwmt`(정적), `f1.nwbt`+F1 몸·얼굴·머리·신발+`f1_combat_attack_onewp01.nwat` | 검사기 통과, Blender 4.2.3에서 가져오기·렌더 확인(스키닝, 공격 동작, 정면 방향, 눈/입 아틀라스 타일) |
| Blender 왕복(가져오기 → Blender 내보내기 → gltf2model/gltf2anim, 30fps) | 7개 메시 정점·면 수 동일, 모든 정점 위치(1e-4)·스킨 가중치(1e-3)·물리 본 5개·텍스처 일치. 동작은 회전 2e-5 / 위치 3e-5 이내로 동일 |

재현:

```bash
python tools/roundtrip_models.py --all D:\isletre\extracted
```
```bash
python tools/roundtrip_anims.py --all D:\isletre\extracted
```

## 8. 남은 질문 (미해결·추론)

- 엔진이 실제로 4번째 가중치를 `1−합`으로 쓰는지: 셰이더 바이너리(`DefaultSkinMesh.fx` 컴파일 결과) 확인 필요. (추론)
- 키 배열 범위를 넘는 `n`(예: `dance_01`의 `n=192`)을 `SetPos`가 무시하는지: `IAnimationKey::SetPos`(0x180065390) 미확인.
- skinmesh `transform`이 스키닝에 쓰이지 않는다는 것: 데이터 비교로만 확인. `ISkinMesh::Render` 확인 필요.
- 1성분 UV(44개 파일)의 v 값: 엔진에서는 정점 버퍼의 이전 내용(초기화 값)으로 남는다. 변환기는 원본 그대로 다시 쓴다.
- 게임이 `IAnimationCtrl::Update`에 넘기는 시간이 실제 밀리초인지(IsletEngine 쪽 호출자 미확인).
- `.nwbt`로의 역변환(본 추가·수정)은 아직 없다. 지금은 원래 스켈레톤에 맞춘 메시·재질·동작만 되돌린다.
