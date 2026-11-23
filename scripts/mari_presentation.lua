-- 마리 위크 전용 확장: 기존 songcard.lua와 같은 카드 태그를 생성한다.
-- songcard.lua가 onSongStart에서 페이드인/아웃을 처리하므로 기존 곡은 변경하지 않는다.
-- Pink Horizon의 스토리 대사는 모두 무음이다. 녹음되지 않은 음성을 재사용하지 않는다.
local FONT = 'NotoSansKR-Medium.otf'
local cards = {
    ['pink-horizon'] = {'Pink Horizon', 'Shinji Ikari  &  Mari Makinami'},
    ['beast-mode'] = {'Beast Mode', 'Shinji Ikari  &  Mari Makinami'},
    ['final-sync'] = {'Final Sync', 'Shinji Ikari  &  Mari Makinami'}
}
local lines = {
    {'mari', '야호! 혼자 노래하고 있었어, 강아지 군?'},
    {'shinji', '으앗… 언제부터 거기 있었어?'},
    {'mari', '방금 왔지. 저 분홍빛 하늘 봐! 노래하기 딱 좋은 날이잖아.'},
    {'shinji', '같이 부르자는 거야? 난 아직 준비가 안 됐는데…'},
    {'mari', '괜찮아, 박자만 타면 돼! 내가 먼저 시작할 테니까 따라와.'},
    {'shinji', '알았어. 이번에는 도망치지 않을게!'}
}
local names = {mari = '마리', shinji = '신지'}
local colors = {mari = 'F455A5', shinji = 'D08A7A'}
local built, active, allow = false, false, false
local idx, shown, typing = 0, 0, false
local chars = {}

local function slug()
    return string.lower(songName):gsub(' ', '-')
end

function onCreatePost()
    local card = cards[slug()]
    if card == nil then return end
    -- 이 태그들은 기존 songcard.lua가 곡 시작 시 애니메이션한다.
    makeLuaText('cardTitle', card[1], 900, 190, 250)
    setTextSize('cardTitle', 64)
    setTextAlignment('cardTitle', 'center')
    setObjectCamera('cardTitle', 'camOther')
    setProperty('cardTitle.alpha', 0)
    addLuaText('cardTitle')
    makeLuaText('cardSub', card[2], 900, 190, 335)
    setTextSize('cardSub', 28)
    setTextAlignment('cardSub', 'center')
    setTextColor('cardSub', 'C8C0E0')
    setObjectCamera('cardSub', 'camOther')
    setProperty('cardSub.alpha', 0)
    addLuaText('cardSub')
end

local function splitChars(s)
    local result = {}
    for c in string.gmatch(s, '[%z\1-\127\194-\244][\128-\191]*') do
        result[#result + 1] = c
    end
    return result
end

local function buildUI()
    if built then return end
    built = true
    makeLuaSprite('mariDlgDim', '', 0, 0)
    makeGraphic('mariDlgDim', 1280, 720, '000000')
    setObjectCamera('mariDlgDim', 'camOther')
    setProperty('mariDlgDim.alpha', 0)
    addLuaSprite('mariDlgDim', true)
    makeLuaSprite('mariDlgBox', '', 80, 470)
    makeGraphic('mariDlgBox', 1120, 190, '12102A')
    setObjectCamera('mariDlgBox', 'camOther')
    setProperty('mariDlgBox.alpha', 0)
    addLuaSprite('mariDlgBox', true)
    makeLuaSprite('mariDlgNameBox', '', 100, 436)
    makeGraphic('mariDlgNameBox', 190, 48, 'F455A5')
    setObjectCamera('mariDlgNameBox', 'camOther')
    setProperty('mariDlgNameBox.alpha', 0)
    addLuaSprite('mariDlgNameBox', true)
    makeLuaText('mariDlgName', '', 190, 100, 443)
    setTextFont('mariDlgName', FONT)
    setTextSize('mariDlgName', 28)
    setTextAlignment('mariDlgName', 'center')
    setObjectCamera('mariDlgName', 'camOther')
    setProperty('mariDlgName.alpha', 0)
    addLuaText('mariDlgName')
    makeLuaText('mariDlgText', '', 1040, 120, 505)
    setTextFont('mariDlgText', FONT)
    setTextSize('mariDlgText', 32)
    setTextAlignment('mariDlgText', 'left')
    setObjectCamera('mariDlgText', 'camOther')
    setProperty('mariDlgText.alpha', 0)
    addLuaText('mariDlgText')
    makeLuaText('mariDlgHint', 'Enter 다음   ESC 건너뛰기', 400, 780, 625)
    setTextFont('mariDlgHint', FONT)
    setTextSize('mariDlgHint', 20)
    setTextColor('mariDlgHint', '9A94C0')
    setTextAlignment('mariDlgHint', 'right')
    setObjectCamera('mariDlgHint', 'camOther')
    setProperty('mariDlgHint.alpha', 0)
    addLuaText('mariDlgHint')
end

local tags = {'mariDlgDim', 'mariDlgBox', 'mariDlgNameBox', 'mariDlgName', 'mariDlgText', 'mariDlgHint'}
local function showLine(i)
    idx = i
    local who, text = lines[i][1], lines[i][2]
    setTextString('mariDlgName', names[who])
    setProperty('mariDlgNameBox.color', getColorFromHex(colors[who]))
    setTextString('mariDlgText', '')
    cameraSetTarget(who == 'shinji' and 'boyfriend' or 'dad')
    chars = splitChars(text)
    shown, typing = 0, true
    runTimer('mariDlgType', 0.035, #chars)
end

local function finishLine()
    cancelTimer('mariDlgType')
    typing = false
    shown = #chars
    setTextString('mariDlgText', table.concat(chars))
end

local function endCutscene()
    if not active then return end
    active = false
    cancelTimer('mariDlgStart')
    cancelTimer('mariDlgType')
    for _, tag in ipairs(tags) do doTweenAlpha(tag .. 'Out', tag, 0, 0.5, 'quadOut') end
    setProperty('camHUD.alpha', 1)
    allow = true
    runTimer('mariDlgGo', 0.55)
end

function onStartCountdown()
    if not allow and isStoryMode and not seenCutscene and slug() == 'pink-horizon' then
        buildUI()
        active = true
        setProperty('camHUD.alpha', 0)
        doTweenAlpha('mariDlgDimIn', 'mariDlgDim', 0.35, 0.6, 'quadOut')
        doTweenAlpha('mariDlgBoxIn', 'mariDlgBox', 0.88, 0.6, 'quadOut')
        for _, tag in ipairs({'mariDlgNameBox', 'mariDlgName', 'mariDlgText', 'mariDlgHint'}) do
            doTweenAlpha(tag .. 'In', tag, 1, 0.6, 'quadOut')
        end
        runTimer('mariDlgStart', 0.8)
        return Function_Stop
    end
    return Function_Continue
end

function onTimerCompleted(tag)
    if tag == 'mariDlgStart' and active then
        showLine(1)
    elseif tag == 'mariDlgType' and active and typing then
        shown = shown + 1
        local text = {}
        for k = 1, shown do text[k] = chars[k] end
        setTextString('mariDlgText', table.concat(text))
        if shown >= #chars then typing = false end
    elseif tag == 'mariDlgGo' and allow then
        setProperty('inCutscene', false)
        startCountdown()
    end
end

function onUpdate(elapsed)
    if not active then return end
    -- ESC는 back 키 설정과 무관하게 항상 건너뛰기; 기존 바인딩도 유지한다.
    if keyboardJustPressed('ESCAPE') or keyJustPressed('back') then
        endCutscene()
    elseif idx > 0 and keyJustPressed('accept') then
        if typing then
            finishLine()
        elseif idx < #lines then
            showLine(idx + 1)
        else
            endCutscene()
        end
    end
end

function onPause()
    if active then return Function_Stop end
    return Function_Continue
end

-- 기존 치트 경고가 호출할 경우 대화 타이머도 함께 중단한다.
function onCheatStart()
    if not active then return end
    active = false
    cancelTimer('mariDlgStart')
    cancelTimer('mariDlgType')
    cancelTimer('mariDlgGo')
    for _, tag in ipairs(tags) do setProperty(tag .. '.alpha', 0) end
end
