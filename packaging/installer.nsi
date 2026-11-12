; NSIS installer for dwg2pdf (Windows x64)
; Built on the GitHub windows runner:
;   makensis /DVER=x.y.z /DDIST=dist\dwg2pdf /DOUTFILE=dwg2pdf-x.y.z-windows-x64-setup.exe packaging\installer.nsi
; Silent install supported: installer.exe /S  (installs to $PROGRAMFILES64\dwg2pdf)

!ifndef VER
  !error "define VER (e.g. /DVER=0.2.0)"
!endif
!ifndef DIST
  !define DIST "dist\dwg2pdf"
!endif
!ifndef OUTFILE
  !define OUTFILE "dwg2pdf-${VER}-windows-x64-setup.exe"
!endif

Unicode true
Name "dwg2pdf ${VER}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\dwg2pdf"
InstallDirRegKey HKLM "Software\dwg2pdf" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include MUI2.nsh
!define MUI_ICON "${__FILEDIR__}icon.ico"
!define MUI_UNICON "${__FILEDIR__}icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\dwg2pdf-gui.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 dwg2pdf 图形界面"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "dwg2pdf" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${DIST}\*.*"

  ; Start Menu
  CreateDirectory "$SMPROGRAMS\dwg2pdf"
  CreateShortcut "$SMPROGRAMS\dwg2pdf\dwg2pdf 图形界面.lnk" "$INSTDIR\dwg2pdf-gui.exe"
  CreateShortcut "$SMPROGRAMS\dwg2pdf\dwg2pdf 命令行.lnk" "$INSTDIR\dwg2pdf.exe"
  CreateShortcut "$SMPROGRAMS\dwg2pdf\卸载.lnk" "$INSTDIR\uninstall.exe"

  ; Add/remove programs entry
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "DisplayName" "dwg2pdf ${VER}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "DisplayVersion" "${VER}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "Publisher" "huanxiang666"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "DisplayIcon" "$INSTDIR\dwg2pdf-gui.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf" "NoRepair" 1
  WriteRegStr HKLM "Software\dwg2pdf" "InstallDir" "$INSTDIR"

  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\dwg2pdf\*.*"
  RMDir "$SMPROGRAMS\dwg2pdf"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\dwg2pdf"
  DeleteRegKey HKLM "Software\dwg2pdf"
SectionEnd
