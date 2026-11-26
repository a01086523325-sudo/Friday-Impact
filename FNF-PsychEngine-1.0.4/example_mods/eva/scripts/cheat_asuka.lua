-- 치트 방지 (봇플레이 / 연습 모드 감지) → 해당 주(week)의 캐릭터가 나와서 대사를 날리고, 3초 뒤 게임 종료
--   아스카 위크 → 아스카 / 레이 위크 → 레이 / 카오루 위크(생일 곡 포함) → 카오루 / 마리 위크 → 마리
--   그 외 곡(미사토 위크 등)에서는 작동하지 않습니다.
-- 설치: mods/eva/scripts/cheat_asuka.lua   (파일 이름은 그대로 두세요: 예전 스크립트를 덮어써야 중복 실행되지 않습니다)
-- 음성: sounds/cheat/<캐릭터>.ogg (edge-tts, tools/make_cheat_voice.py 로 생성) 가 있으면 그것을 재생하고,
--       없으면 아스카는 sounds/asuka_bad_user_fast.ogg, 나머지는 말소리 효과음(sounds/cheat/blip_*.ogg)만 납니다.
-- (컷씬 음성 중단: scripts/story_cutscene.lua, 생일 입력 창 닫기: stages/birthdayroom.lua 도 함께 두세요)

local ENABLED        = true    -- false 로 바꾸면 이 기능을 끕니다(테스트용)
local CHEAT_BOTPLAY  = false   -- 봇플레이를 치트로 취급
local CHEAT_PRACTICE = true    -- 연습 모드를 치트로 취급
local QUIT_SECONDS   = 3       -- 캐릭터가 나오고 몇 초 뒤에 종료할지
local PAUSE_SONG     = true    -- 캐릭터가 나올 때 노래·노트 진행을 멈춤
local FIT_QUIT_TO_VOICE = true   -- 음성이 QUIT_SECONDS 보다 길면, 음성이 끝난 뒤(+0.1초)에 종료되도록 종료 시간을 늘림
local MAX_QUIT       = 6       -- 종료까지 최대 몇 초까지 늘릴지
local TYPE_FILL      = 0.85    -- 대사 타자가 음성 길이의 몇 %까지 끝나도록 속도를 맞출지(0.85 = 음성이 끝나기 조금 전에 대사 완성)
local TYPE_DEFAULT   = 0.045   -- 음성이 없을 때 한 글자가 나오는 간격(초)

-- 이 두 볼륨 값으로 '치트 방지 음성'과 '말소리 효과음'만 골라 남기고 나머지 소리를 모두 끔 (사실상 1.0)
local VOICE_VOLUME   = 0.9999
local BLIP_VOLUME    = 0.9998

local FONT = 'NotoSansKR-Medium.otf'

-- 곡 → 캐릭터 (곡 이름은 소문자, 하이픈은 공백으로 비교)
local SONG_CAST = {
    ['anta baka'] = 'asuka', ['hedgehog dilemma'] = 'asuka', ['sortie'] = 'asuka',
    ['unit zero'] = 'rei', ['smile'] = 'rei', ['blue moon'] = 'rei',
    ['piano duet'] = 'kaoru', ['daiku'] = 'kaoru', ['tabris'] = 'kaoru', ['birthday party'] = 'kaoru',
    ['pink horizon'] = 'mari', ['beast mode'] = 'mari', ['final sync'] = 'mari',
}

-- 캐릭터별 설정. msg 를 고치면 tools/make_cheat_voice.py 를 다시 실행해 음성을 새로 만드세요.
-- (한 항목 안에 중괄호를 쓰지 마세요: 음성 생성 도구가 이 표를 읽습니다)
local CAST = {
    asuka = { name = '아스카', color = 'C94A3A', sheet = 'characters/asuka', h = 454,
              clip = 'asuka_bad_user_fast',
              msg = '안타 바카?! 감히 내 파일을 조작하려 들다니, 한심하긴!' },
    rei   = { name = '레이', color = '5A8FC8', sheet = 'characters/rei', h = 1083,
              msg = '그건 네 힘이 아니야. 그만둬, 이카리 군.' },
    kaoru = { name = '카오루', color = '8A80D0', sheet = 'characters/kaoru', h = 439,
              msg = '치트라니, 실망이야. 잘 가, 신지 군.' },
    mari  = { name = '마리', color = 'D8508A', sheet = 'characters/mari', h = 454,
              msg = '어머, 반칙이야! 벌로 게임은 끝, 바이바이~' },
}

local who = nil          -- 이번 곡의 치트 방지 캐릭터 키 (없으면 기능 꺼짐)
local triggered = false
local typedChars, typedShown = {}, 0
local hasVoice = false   -- 치트 방지 음성(TTS/클립)이 실제로 재생되는지

------------------------------------------------------------------ 유틸
local function isCheating()
    if CHEAT_BOTPLAY and getProperty('cpuControlled') == true then return true end
    if CHEAT_PRACTICE and getProperty('practiceMode') == true then return true end
    return false
end

local function splitUtf8(s)
    local t = {}
    for i = 1, #s do
        local b = string.byte(s, i)
        if b < 128 or b >= 192 then
            t[#t + 1] = string.sub(s, i, i)
        else
            t[#t] = t[#t] .. string.sub(s, i, i)
        end
    end
    return t
end

local function quitGame()
    -- 환경에 따라 되는 방법이 다를 수 있어 순서대로 시도합니다
    pcall(function() os.exit(0) end)
    pcall(function() runHaxeCode("Sys.exit(0);") end)
    pcall(function() runHaxeCode("import lime.system.System; System.exit(0);") end)
end

------------------------------------------------------------------ 소리 정리
-- 치트 방지 음성·말소리 효과음(VOICE_VOLUME / BLIP_VOLUME 으로 구분)만 빼고 모든 소리를 끔
-- 노래·보컬·컷씬 음성·메뉴 효과음 등 재생 중인 소리 전부
local function silenceAllExceptCheat()
    pcall(function()
        runHaxeCode([[
            for (s in FlxG.sound.list.members) {
                if (s != null && s.playing) {
                    var v = s.volume;
                    if (Math.abs(v - ]] .. VOICE_VOLUME .. [[) > 0.00004 && Math.abs(v - ]] .. BLIP_VOLUME .. [[) > 0.00004) s.stop();
                }
            }
            if (FlxG.sound.music != null) FlxG.sound.music.pause();
        ]])
    end)
end
local silenceTimer = 0

-- 대사 출력(컷씬 대화창·타이핑, 생일 입력 창 글자)을 전부 숨기고 진행 타이머/트윈을 취소
local DIALOGUE_TAGS = {'dlgDim', 'dlgBox', 'dlgNameBox', 'dlgName', 'dlgText', 'dlgHint',
    'inDim', 'inPanelB', 'inPanel', 'inTitle', 'inSub', 'inBox1', 'inBox2', 'inBox3', 'inBox4',
    'inDigit1', 'inDigit2', 'inDigit3', 'inDigit4', 'inStatus', 'inHint',
    'mariDlgDim', 'mariDlgBox', 'mariDlgNameBox', 'mariDlgName', 'mariDlgText', 'mariDlgHint'}
local DIALOGUE_TIMERS = {'dlgStart', 'dlgType', 'dlgGo', 'partyGo', 'partyStart', 'inReset',
    'mariDlgStart', 'mariDlgType', 'mariDlgGo'}

local function hideDialogue()
    for _, t in ipairs(DIALOGUE_TIMERS) do pcall(function() cancelTimer(t) end) end
    for _, t in ipairs(DIALOGUE_TAGS) do
        if luaSpriteExists(t) or luaTextExists(t) then
            pcall(function() cancelTween(t .. 'In') end)
            pcall(function() cancelTween(t .. 'Out') end)
            pcall(function() setProperty(t .. '.alpha', 0) end)
        end
    end
end

local function stopCutsceneVoice()
    -- 컷씬 대사 음성·말소리 중단 (story_cutscene.lua 의 onCheatStart 도 함께 호출)
    pcall(function() stopSound('cutVoice') end)
    pcall(function() cancelTimer('dlgType') end)
    pcall(function() callOnLuas('onCheatStart', {}) end)
end

local function pauseSong()
    -- 1) 게임 진행(노트 이동·박자)을 멈춤: PlayState 의 paused 가 true 면 Conductor 시간이 흐르지 않음
    pcall(function() setProperty('paused', true) end)
    -- 2) 노래 음원(반주·보컬)을 일시정지
    pcall(function()
        runHaxeCode([[
            if (FlxG.sound.music != null) FlxG.sound.music.pause();
            if (game.vocals != null) game.vocals.pause();
            if (game.opponentVocals != null) game.opponentVocals.pause();
        ]])
    end)
    -- 3) 혹시 위 방법이 막혀 있어도 소리는 나지 않게 볼륨 0
    pcall(function() setPropertyFromClass('flixel.FlxG', 'sound.music.volume', 0) end)
    pcall(function() setProperty('vocals.volume', 0) end)
end

------------------------------------------------------------------ 화면 구성
local function makeBox(tag, w, h, color, x, y, alpha)
    makeLuaSprite(tag, '', x, y)
    makeGraphic(tag, w, h, color)
    setProperty(tag .. '.alpha', alpha)
    setObjectCamera(tag, 'other')
    addLuaSprite(tag, true)
end

-- 다른 창(2차 인증 창 등)보다 항상 위에 보이도록, 가장 위쪽 전용 카메라를 만들어 캐릭터 화면을 옮김
local CHEAT_TAGS = {'cheatDim', 'cheatChar', 'cheatBubbleBorder', 'cheatBubble', 'cheatNameBox',
                    'cheatName', 'cheatText', 'cheatCount'}
local topTimer = 0

local function assignTopCamera()
    local list = ''
    for i, t in ipairs(CHEAT_TAGS) do list = list .. (i > 1 and ',' or '') .. "'" .. t .. "'" end
    pcall(function()
        runHaxeCode([[
            var cam = new FlxCamera();
            cam.bgColor = 0x00000000;
            cam.zoom = 1.0001;
            FlxG.cameras.add(cam, false);
            for (t in []] .. list .. [[]) {
                var o = game.getLuaObject(t);
                if (o != null) o.cameras = [cam];
            }
        ]])
    end)
end

local function keepOnTop()
    pcall(function()
        runHaxeCode([[
            for (c in FlxG.cameras.list) {
                if (c.zoom > 1.00005 && c.zoom < 1.00015) {
                    FlxG.cameras.remove(c, false);
                    FlxG.cameras.add(c, false);
                    break;
                }
            }
        ]])
    end)
end

------------------------------------------------------------------ 음성
local function playCheatVoice(key, cfg)
    -- 1) edge-tts 음성(sounds/cheat/<키>.ogg)이 있으면 우선 재생, 2) 없으면 설정된 클립(아스카)
    local hasTts = false
    pcall(function() hasTts = checkFileExists('sounds/cheat/' .. key .. '.ogg') == true end)
    if hasTts then
        pcall(function() playSound('cheat/' .. key, VOICE_VOLUME, 'cheatVoice') end)
        hasVoice = true
    elseif cfg.clip then
        pcall(function() playSound(cfg.clip, VOICE_VOLUME, 'cheatVoice') end)
        hasVoice = true
    end
end

-- 재생 중인 치트 방지 음성의 길이(초). 못 재면 nil
local function measureVoice()
    local ms = nil
    pcall(function()
        ms = runHaxeCode([[
            var cheatLen = 0.0;
            for (s in FlxG.sound.list.members) {
                if (s != null && s.playing && Math.abs(s.volume - ]] .. VOICE_VOLUME .. [[) < 0.00004) cheatLen = s.length;
            }
            cheatLen;
        ]])
    end)
    if type(ms) == 'number' and ms > 300 then return ms / 1000 end
    return nil
end

local function blip(key)
    if hasVoice then return end   -- 음성이 나올 때는 말소리 효과음을 겹치지 않음
    pcall(function() playSound('cheat/blip_' .. key .. '_' .. math.random(1, 4), BLIP_VOLUME) end)
end

------------------------------------------------------------------ 발동
local function triggerCheat()
    triggered = true
    local cfg = CAST[who]
    setProperty('canPause', false)

    -- 컷씬 음성 중단 + 대사 출력 숨김
    stopCutsceneVoice()
    hideDialogue()

    -- 노래 일시정지
    if PAUSE_SONG then pauseSong() end

    -- 치트 방지 음성 외 모든 소리 끄기 → 그 다음 캐릭터 음성 재생
    silenceAllExceptCheat()
    playCheatVoice(who, cfg)

    -- 화면 어둡게
    makeBox('cheatDim', 1280, 720, '000000', 0, 0, 0)
    doTweenAlpha('cheatDimIn', 'cheatDim', 0.8, 0.25, 'quadOut')

    -- 캐릭터 등장 (아래에서 올라옴). 프레임 높이(h)에 맞춰 화면 높이의 약 90%로 키움
    local scale = 650 / cfg.h
    local finalY = 720 - 650 + 15
    makeAnimatedLuaSprite('cheatChar', cfg.sheet, 110, finalY + 90)
    addAnimationByPrefix('cheatChar', 'idle', 'idle', 6, true)
    objectPlayAnimation('cheatChar', 'idle', true)
    scaleObject('cheatChar', scale, scale)
    setProperty('cheatChar.alpha', 0)
    setObjectCamera('cheatChar', 'other')
    addLuaSprite('cheatChar', true)
    doTweenAlpha('cheatCharA', 'cheatChar', 1, 0.3, 'quadOut')
    doTweenY('cheatCharY', 'cheatChar', finalY, 0.35, 'backOut')

    -- 말풍선 + 이름표
    makeBox('cheatBubbleBorder', 676, 296, cfg.color, 552, 156, 0)
    makeBox('cheatBubble', 664, 284, 'FFFFFF', 558, 162, 0)
    makeBox('cheatNameBox', 190, 44, cfg.color, 552, 112, 0)
    doTweenAlpha('cheatBubbleBorderA', 'cheatBubbleBorder', 1, 0.3, 'quadOut')
    doTweenAlpha('cheatBubbleA', 'cheatBubble', 1, 0.3, 'quadOut')
    doTweenAlpha('cheatNameBoxA', 'cheatNameBox', 1, 0.3, 'quadOut')

    makeLuaText('cheatName', cfg.name, 190, 552, 118)
    setTextFont('cheatName', FONT)
    setTextSize('cheatName', 28)
    setTextColor('cheatName', 'FFFFFF')
    setTextAlignment('cheatName', 'center')
    setObjectCamera('cheatName', 'other')
    addLuaText('cheatName')

    -- 대사: 한 글자씩 타자 효과 + 말소리 효과음
    typedChars = splitUtf8(cfg.msg)
    typedShown = 0
    makeLuaText('cheatText', '', 630, 575, 190)
    setTextFont('cheatText', FONT)
    setTextSize('cheatText', 36)
    setTextColor('cheatText', '000000')
    setTextAlignment('cheatText', 'left')
    setObjectCamera('cheatText', 'other')
    addLuaText('cheatText')

    makeLuaText('cheatCount', QUIT_SECONDS .. '초 뒤에 게임이 종료됩니다', 630, 575, 380)
    setTextFont('cheatCount', FONT)
    setTextSize('cheatCount', 28)
    setTextColor('cheatCount', 'D61C24')
    setTextAlignment('cheatCount', 'left')
    setObjectCamera('cheatCount', 'other')
    addLuaText('cheatCount')

    assignTopCamera()
    runTimer('cheatMeasure', 0.12)   -- 음성이 재생되기 시작하면 길이를 재서 타자 속도·종료 시간을 음성에 맞춤
end

------------------------------------------------------------------ 콜백
function onCreate()
    local key = string.lower(songName or '')
    key = (string.gsub(key, '%-', ' '))
    who = SONG_CAST[key]
end

-- 치트가 처음부터 켜져 있으면 카운트다운·노래 시작 자체를 막고 캐릭터를 먼저 띄움
-- (카운트다운이 끝나면 노래가 새로 재생되므로, 시작 전에 막지 않으면 노래가 캐릭터 창과 함께 켜짐)
function onStartCountdown()
    if who and ENABLED and isCheating() then
        if not triggered then triggerCheat() end
        return Function_Stop
    end
    return Function_Continue
end

-- 혹시 노래가 시작돼 버렸다면 다시 멈춤
function onSongStart()
    if triggered and PAUSE_SONG then pauseSong() end
end

function onUpdatePost(elapsed)
    if who and ENABLED and not triggered and isCheating() then
        triggerCheat()
    end
    if triggered then
        silenceTimer = silenceTimer + elapsed
        if silenceTimer > 0.05 then silenceTimer = 0; silenceAllExceptCheat(); hideDialogue() end   -- 새로 나는 소리·대사도 계속 끔
        topTimer = topTimer + elapsed
        if topTimer > 0.2 then topTimer = 0; keepOnTop() end   -- 0.2초마다 맨 위로 다시 올림
    end
end

function onTimerCompleted(tag, loops, loopsLeft)
    if tag == 'cheatMeasure' then
        local len = measureVoice()
        if not len and hasVoice then len = QUIT_SECONDS * 0.95 end       -- 길이를 못 재면 종료 시간에 맞춰 가정
        -- 대사 타자: 음성이 끝나기 조금 전에 대사가 모두 나오도록 글자 간격을 맞춤
        local interval = TYPE_DEFAULT
        if len and #typedChars > 0 then interval = math.max(0.02, math.min(0.12, len * TYPE_FILL / #typedChars)) end
        runTimer('cheatType', interval, #typedChars)
        -- 종료 시간: 기본 QUIT_SECONDS, 음성이 더 길면 음성이 끝난 뒤(+0.1초 여유)로 늘림
        local secs = QUIT_SECONDS
        if FIT_QUIT_TO_VOICE and len then
            secs = math.max(QUIT_SECONDS, math.min(MAX_QUIT, math.ceil(len + 0.1 - 0.12)))
        end
        setTextString('cheatCount', secs .. '초 뒤에 게임이 종료됩니다')
        runTimer('cheatQuit', 1, secs)
    elseif tag == 'cheatType' then
        typedShown = typedShown + 1
        setTextString('cheatText', table.concat(typedChars, '', 1, typedShown))
        if typedShown % 2 == 1 and typedChars[typedShown] ~= ' ' then blip(who) end
    elseif tag == 'cheatQuit' then
        if loopsLeft <= 0 then
            quitGame()
        else
            setTextString('cheatCount', loopsLeft .. '초 뒤에 게임이 종료됩니다')
        end
    end
end
