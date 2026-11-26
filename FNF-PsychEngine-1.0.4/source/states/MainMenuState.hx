package states;

import flixel.FlxObject;
import flixel.effects.FlxFlicker;
import lime.app.Application;
import states.editors.MasterEditorMenu;
import options.OptionsState;
import flixel.tweens.FlxTween;
import flixel.tweens.FlxEase;
import flixel.util.FlxColor;
import flixel.math.FlxMath;
// ===== 컴파일 및 인게임 진입을 위해 추가된 Import =====
import states.PlayState;
import backend.Song;
import backend.Mods;
import backend.ClientPrefs;
import backend.Achievements;

enum MainMenuColumn {
	LEFT;
	CENTER;
	RIGHT;
}

class MainMenuState extends MusicBeatState
{
	public static var psychEngineVersion:String = '1.0.4';
	public static var curSelected:Int = 0;
	public static var curColumn:MainMenuColumn = CENTER;
	var allowMouse:Bool = true;

	var menuItems:FlxTypedGroup<FlxSprite>;
	var leftItem:FlxSprite;
	var rightItem:FlxSprite;

	var optionShit:Array<String> = [
		'story_mode',
		'freeplay',
		#if MODS_ALLOWED 'mods', #end
		'credits'
	];

	var leftOption:String = #if ACHIEVEMENTS_ALLOWED 'achievements' #else null #end;
	var rightOption:String = 'options';

	var magenta:FlxSprite;
	var camFollow:FlxObject;

	static var showOutdatedWarning:Bool = true;

	// ===== 카오루 생일 입력창 변수 및 트윈 제어기 =====
	var kaoruTyped:String = '';
	var bdayBox:FlxSprite;
	var bdayPrompt:FlxText;
	var bdayInput:FlxText;
	var birthdayOn:Bool = false;

	var bdayBoxTween:FlxTween;
	var bdayPromptTween:FlxTween;
	var bdayInputTween:FlxTween;

	override function create()
	{
		super.create();

		#if MODS_ALLOWED
		Mods.pushGlobalMods();
		#end
		Mods.loadTopMod();

		#if DISCORD_ALLOWED
		DiscordClient.changePresence("In the Menus", null);
		#end

		persistentUpdate = persistentDraw = true;

		var yScroll:Float = 0.25;
		var bg:FlxSprite = new FlxSprite(-80).loadGraphic(Paths.image('menuBG'));
		bg.antialiasing = ClientPrefs.data.antialiasing;
		bg.scrollFactor.set(0, yScroll);
		bg.setGraphicSize(Std.int(bg.width * 1.175));
		bg.updateHitbox();
		bg.screenCenter();
		add(bg);

		camFollow = new FlxObject(0, 0, 1, 1);
		add(camFollow);

		magenta = new FlxSprite(-80).loadGraphic(Paths.image('menuDesat'));
		magenta.antialiasing = ClientPrefs.data.antialiasing;
		magenta.scrollFactor.set(0, yScroll);
		magenta.setGraphicSize(Std.int(magenta.width * 1.175));
		magenta.updateHitbox();
		magenta.screenCenter();
		magenta.visible = false;
		magenta.color = 0xFFfd719b;
		add(magenta);

		menuItems = new FlxTypedGroup<FlxSprite>();
		add(menuItems);

		for (num => option in optionShit)
		{
			var item:FlxSprite = createMenuItem(option, 0, (num * 140) + 90);
			item.y += (4 - optionShit.length) * 70;
			item.screenCenter(X);
		}

		if (leftOption != null)
			leftItem = createMenuItem(leftOption, 60, 490);
		if (rightOption != null)
		{
			rightItem = createMenuItem(rightOption, FlxG.width - 60, 490);
			rightItem.x -= rightItem.width;
		}

		var psychVer:FlxText = new FlxText(12, FlxG.height - 44, 0, "Psych Engine v" + psychEngineVersion, 12);
		psychVer.scrollFactor.set();
		psychVer.setFormat(Paths.font("vcr.ttf"), 16, FlxColor.WHITE, LEFT, FlxTextBorderStyle.OUTLINE, FlxColor.BLACK);
		add(psychVer);
		var fnfVer:FlxText = new FlxText(12, FlxG.height - 24, 0, "Friday Night Funkin' v" + Application.current.meta.get('version'), 12);
		fnfVer.scrollFactor.set();
		fnfVer.setFormat(Paths.font("vcr.ttf"), 16, FlxColor.WHITE, LEFT, FlxTextBorderStyle.OUTLINE, FlxColor.BLACK);
		add(fnfVer);

		// ===== 카오루 생일 입력창 생성 (초기 상태 visible = false 철저히 가림) =====
		bdayBox = new FlxSprite(0, 20).makeGraphic(500, 120, 0xCC000000);
		bdayBox.screenCenter(X);
		bdayBox.scrollFactor.set(0, 0);
		bdayBox.alpha = 0;
		bdayBox.visible = false; 
		add(bdayBox);

		bdayPrompt = new FlxText(0, 35, 500, '카오루 생일 입력 (0913)', 24);
		bdayPrompt.setFormat(Paths.font("vcr.ttf"), 24, FlxColor.WHITE, CENTER);
		bdayPrompt.screenCenter(X);
		bdayPrompt.scrollFactor.set(0, 0);
		bdayPrompt.alpha = 0;
		bdayPrompt.visible = false;
		add(bdayPrompt);

		bdayInput = new FlxText(0, 75, 500, '', 36);
		bdayInput.setFormat(Paths.font("vcr.ttf"), 36, 0xFFFFDD88, CENTER);
		bdayInput.screenCenter(X);
		bdayInput.scrollFactor.set(0, 0);
		bdayInput.alpha = 0;
		bdayInput.visible = false;
		add(bdayInput);

		changeItem();

		#if ACHIEVEMENTS_ALLOWED
		var leDate = Date.now();
		if (leDate.getDay() == 5 && leDate.getHours() >= 18)
			Achievements.unlock('friday_night_play');

		#if MODS_ALLOWED
		Mods.pushGlobalMods();
		Achievements.reloadList();
		#end
		#end

		#if CHECK_FOR_UPDATES
		if (showOutdatedWarning && ClientPrefs.data.checkForUpdates && substates.OutdatedSubState.updateVersion != psychEngineVersion) {
			persistentUpdate = false;
			showOutdatedWarning = false;
			openSubState(new substates.OutdatedSubState());
		}
		#end

		// ===== 메인 메뉴 진입 시 마우스 활성화 및 커서 가시화 =====
		FlxG.mouse.visible = true;

		FlxG.camera.follow(camFollow, null, 0.15);
	}

	function createMenuItem(name:String, x:Float, y:Float):FlxSprite
	{
		var menuItem:FlxSprite = new FlxSprite(x, y);
		menuItem.frames = Paths.getSparrowAtlas('mainmenu/menu_$name');
		menuItem.animation.addByPrefix('idle', '$name idle', 24, true);
		menuItem.animation.addByPrefix('selected', '$name selected', 24, true);
		menuItem.animation.play('idle');
		menuItem.updateHitbox();
		
		menuItem.antialiasing = ClientPrefs.data.antialiasing;
		menuItem.scrollFactor.set();
		menuItems.add(menuItem);
		return menuItem;
	}

	var selectedSomethin:Bool = false;
	var timeNotMoving:Float = 0;

	override function update(elapsed:Float)
	{
		// ===== 카오루 생일 입력 및 페이드 연출 제어 파트 =====
		if (!birthdayOn && !selectedSomethin)
		{
			var k = FlxG.keys.firstJustPressed();
			if (k >= 0)
			{
				var d:Int = -1;
				if (k >= 48 && k <= 57) d = k - 48;
				else if (k >= 96 && k <= 105) d = k - 96;

				if (d >= 0)
				{
					// 숫자를 치기 시작하면 그제서야 visible을 켜서 화면에 노출시킵니다.
					if (kaoruTyped.length == 0)
					{
						bdayBox.visible = true;
						bdayPrompt.visible = true;
						bdayInput.visible = true;
						fadeBdayUI(1.0, 0.25);
					}

					kaoruTyped += Std.string(d);
					if (kaoruTyped.length > 8)
						kaoruTyped = kaoruTyped.substr(kaoruTyped.length - 8);
					bdayInput.text = kaoruTyped;

					if (StringTools.endsWith(kaoruTyped, '0913') || StringTools.endsWith(kaoruTyped, '20000913'))
						startKaoruBirthday();
				}
				else if (k == 8) // Backspace
				{
					if (kaoruTyped.length > 0)
					{
						kaoruTyped = kaoruTyped.substr(0, kaoruTyped.length - 1);
						bdayInput.text = kaoruTyped;
					}
					
					if (kaoruTyped.length == 0)
						fadeBdayUI(0.0, 0.2);
				}
				else
				{
					if (kaoruTyped.length > 0)
					{
						kaoruTyped = '';
						bdayInput.text = '';
						fadeBdayUI(0.0, 0.2);
					}
				}
			}
		}

		if (FlxG.sound.music != null && FlxG.sound.music.volume < 0.8)
			FlxG.sound.music.volume = Math.min(FlxG.sound.music.volume + 0.5 * elapsed, 0.8);
		
		if (!selectedSomethin)
		{
			// 키보드 입력 감지
			if (controls.UI_UP_P) changeItem(-1);
			if (controls.UI_DOWN_P) changeItem(1);

			if (controls.UI_LEFT_P)
			{
				if (leftOption != null && curColumn == CENTER) {
					curColumn = LEFT;
					changeItem();
				} else if (curColumn == RIGHT) {
					curColumn = CENTER;
					changeItem();
				}
			}
			if (controls.UI_RIGHT_P)
			{
				if (rightOption != null && curColumn == CENTER) {
					curColumn = RIGHT;
					changeItem();
				} else if (curColumn == LEFT) {
					curColumn = CENTER;
					changeItem();
				}
			}

			if (controls.BACK)
			{
				selectedSomethin = true;
				FlxG.sound.play(Paths.sound('cancelMenu'));
				MusicBeatState.switchState(new TitleState());
			}

			if (controls.ACCEPT)
			{
				selectSomething();
			}
			#if desktop
			else if (FlxG.keys.anyJustPressed([SEVEN]))
			{
				selectedSomethin = true;
				MusicBeatState.switchState(new MasterEditorMenu());
			}
			#end

			// ===== 마우스 조작 피드백 및 클릭 처리 =====
			if (allowMouse && !birthdayOn && kaoruTyped.length == 0 && (FlxG.mouse.deltaX != 0 || FlxG.mouse.deltaY != 0 || FlxG.mouse.justPressed))
			{
				// 1. 중앙 세로 메뉴 호버링 처리
				for (i in 0...menuItems.members.length)
				{
					var item = menuItems.members[i];
					if (item != null && FlxG.mouse.overlaps(item))
					{
						if ((FlxG.mouse.deltaX != 0 || FlxG.mouse.deltaY != 0) && (curSelected != i || curColumn != CENTER))
						{
							curColumn = CENTER;
							curSelected = i;
							changeItem();
						}
						if (FlxG.mouse.justPressed)
						{
							selectSomething();
						}
					}
				}

				// 2. 왼쪽 메뉴 아이템 호버링 및 클릭 처리
				if (leftItem != null && FlxG.mouse.overlaps(leftItem))
				{
					if ((FlxG.mouse.deltaX != 0 || FlxG.mouse.deltaY != 0) && curColumn != LEFT)
					{
						curColumn = LEFT;
						changeItem();
					}
					if (FlxG.mouse.justPressed)
					{
						selectSomething();
					}
				}

				// 3. 오른쪽 메뉴 아이템 호버링 및 클릭 처리
				if (rightItem != null && FlxG.mouse.overlaps(rightItem))
				{
					if ((FlxG.mouse.deltaX != 0 || FlxG.mouse.deltaY != 0) && curColumn != RIGHT)
					{
						curColumn = RIGHT;
						changeItem();
					}
					if (FlxG.mouse.justPressed)
					{
						selectSomething();
					}
				}
			}
		}
		super.update(elapsed);
	}

	// ===== 메뉴 선택(진입) 공용 처리 함수 =====
	function selectSomething():Void
	{
		selectedSomethin = true;
		FlxG.sound.play(Paths.sound('confirmMenu'));

		if (ClientPrefs.data.flashing) FlxFlicker.flicker(magenta, 1.1, 0.15, false);

		var targetItem:FlxSprite = null;
		var optionName:String = '';

		switch (curColumn)
		{
			case CENTER:
				targetItem = menuItems.members[curSelected];
				optionName = optionShit[curSelected];
			case LEFT:
				targetItem = leftItem;
				optionName = leftOption;
			case RIGHT:
				targetItem = rightItem;
				optionName = rightOption;
		}

		FlxFlicker.flicker(targetItem, 1, 0.06, false, false, function(flick:FlxFlicker)
		{
			switch (optionName)
			{
				case 'story_mode':
					MusicBeatState.switchState(new StoryMenuState());
				case 'freeplay':

MusicBeatState.switchState(new FreeplayState());
#if MODS_ALLOWED
case 'mods':
MusicBeatState.switchState(new ModsMenuState());
#end
case 'credits':
MusicBeatState.switchState(new CreditsState());
case 'achievements':
MusicBeatState.switchState(new AchievementsMenuState());
case 'options':
LoadingState.loadAndSwitchState(new OptionsState());
}
});
for (item in menuItems.members)
{
if (item == targetItem) continue;
FlxTween.tween(item, {alpha: 0}, 0.4, {
ease: FlxEase.quadOut,
onComplete: function(twn:FlxTween) {
item.kill();
}
});
}
if (leftItem != null && leftItem != targetItem) FlxTween.tween(leftItem, {alpha: 0}, 0.4, {ease: FlxEase.quadOut});
if (rightItem != null && rightItem != targetItem) FlxTween.tween(rightItem, {alpha: 0}, 0.4, {ease: FlxEase.quadOut});
}
// ===== 생일 입력 성공 시 곡 플레이 화면으로 전환되는 핵심 함수 =====
function startKaoruBirthday():Void
{
birthdayOn = true;
kaoruTyped = '';
bdayPrompt.text = 'Happy Birthday, Kaoru!';
bdayInput.text = '생일 축하해!!';
selectedSomethin = true;
allowMouse = false;
if (FlxG.sound.music != null)
FlxG.sound.music.stop();
// 인게임 차트(Birthday Party) 데이터 로드 및 적용
var songLowercase:String = Paths.formatToSongPath('Birthday Party');
PlayState.SONG = Song.loadFromJson(songLowercase, songLowercase);
PlayState.isStoryMode = false;
PlayState.storyDifficulty = 1; // 1 = Normal 난이도 지정
FlxG.sound.play(Paths.sound('confirmMenu'));
// 하얗게 반짝이는 플리커(Flicker) 연출이 '끝난 직후' 완벽히 파괴하고 넘어가도록 설계
FlxFlicker.flicker(bdayBox, 0.6, 0.06, false, false, function(flick:FlxFlicker)
{
if (bdayBoxTween != null) bdayBoxTween.cancel();
if (bdayPromptTween != null) bdayPromptTween.cancel();
if (bdayInputTween != null) bdayInputTween.cancel();
// 씬 전환 그리기 간섭 차단
persistentDraw = false;
persistentUpdate = false;
bdayBox.visible = false;
bdayPrompt.visible = false;
bdayInput.visible = false;
remove(bdayBox);
remove(bdayPrompt);
remove(bdayInput);
bdayBox.destroy();
bdayPrompt.destroy();
bdayInput.destroy();
// 마우스 커서도 인게임 플레이 중에는 완벽히 가려줍니다.
FlxG.mouse.visible = false;
// 플레이스테이트로 안전하게 진입시킵니다.
MusicBeatState.switchState(new PlayState());
});
}
function stopKaoruBirthday():Void
{
birthdayOn = false;
bdayPrompt.text = '카오루 생일 입력 (0913)';
bdayInput.text = '';
FlxG.sound.playMusic(Paths.music('freakyMenu'), 0.7, true);
fadeBdayUI(0.0, 0.25);
}
function fadeBdayUI(targetAlpha:Float, duration:Float)
{
if (bdayBoxTween != null) bdayBoxTween.cancel();
if (bdayPromptTween != null) bdayPromptTween.cancel();
if (bdayInputTween != null) bdayInputTween.cancel();
var onComp = function(twn:FlxTween) {
if (targetAlpha == 0) {
bdayBox.visible = false;
bdayPrompt.visible = false;
bdayInput.visible = false;
}
};
bdayBoxTween = FlxTween.tween(bdayBox, {alpha: targetAlpha}, duration, {ease: FlxEase.quadOut, onComplete: onComp});
bdayPromptTween = FlxTween.tween(bdayPrompt, {alpha: targetAlpha}, duration, {ease: FlxEase.quadOut});
bdayInputTween = FlxTween.tween(bdayInput, {alpha: targetAlpha}, duration, {ease: FlxEase.quadOut});
}
function changeItem(change:Int = 0)
{
if(change != 0) curColumn = CENTER;
curSelected = FlxMath.wrap(curSelected + change, 0, optionShit.length - 1);
FlxG.sound.play(Paths.sound('scrollMenu'));
for (item in menuItems)
{
item.animation.play('idle');
item.centerOffsets();
}
var selectedItem:FlxSprite;
switch(curColumn)
{
case CENTER:
selectedItem = menuItems.members[curSelected];
case LEFT:
selectedItem = leftItem;
case RIGHT:
selectedItem = rightItem;
}
selectedItem.animation.play('selected');
selectedItem.centerOffsets();
camFollow.y = selectedItem.getGraphicMidpoint().y;
}
}