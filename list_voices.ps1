Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach($v in $s.GetInstalledVoices()){
    $info = $v.VoiceInfo
    Write-Output ($info.Name + "|" + $info.Culture.Name + "|" + $info.Gender)
}
$s.Dispose()
