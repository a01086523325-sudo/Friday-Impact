-- Final Sync: 임팩트. 2816x1584 이미지를 비율을 보존하여 폭 2600으로 맞춘다.
-- 기존 배경의 가로 범위(-660~1940)와 캐릭터 좌표를 유지한다.
-- 바닥은 기존 신지의 발(약 y=656) 아래쪽에 오도록 개별 조정한다.
function onCreate()
    makeLuaSprite('mariStageBackground', 'stages/mari-impact/background', -660, -510)
    scaleObject('mariStageBackground', 2600 / 2816, 2600 / 2816)
    setScrollFactor('mariStageBackground', 1, 1)
    -- false: 무대 이미지는 신지/마리 등 모든 캐릭터 뒤에 배치한다.
    addLuaSprite('mariStageBackground', false)
end
