@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo   Prosp - demarrage
echo ==================================================
echo.

REM Cherche python, puis py (le lanceur officiel Windows) si python est absent.
where python >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set "PY=py"
    ) else (
        echo Python n'est pas trouve sur cette machine.
        echo Installe-le depuis https://python.org - coche bien la case
        echo "Add python.exe to PATH" pendant l'installation - puis relance
        echo ce fichier.
        echo.
        pause
        exit /b 1
    )
)

REM Installe les dependances seulement si necessaire (ne ralentit pas les
REM lancements suivants une fois que tout est deja installe).
%PY% -c "import flask, anthropic, yaml, dotenv" >nul 2>&1
if errorlevel 1 (
    echo Premiere installation des dependances - un peu plus long cette fois...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo L'installation a echoue. Verifie ta connexion internet et que
        echo pip fonctionne correctement, puis relance ce fichier.
        pause
        exit /b 1
    )
    echo.
)

echo Demarrage du serveur dans une fenetre separee...
start "Prosp - serveur (ne pas fermer tant que tu utilises l'app)" /min cmd /c "%PY% -m dashboard.app"

REM Laisse le serveur quelques secondes pour demarrer avant d'ouvrir le
REM navigateur. Si la page ne charge pas encore, attends 2-3 secondes et
REM rafraichis - le serveur est presque surement pret entre-temps.
timeout /t 4 /nobreak >nul

start http://127.0.0.1:5001

echo.
echo Prosp est ouvert dans ton navigateur.
echo.
echo IMPORTANT : une fenetre "Prosp - serveur" tourne en arriere-plan
echo (reduite dans la barre des taches). Ne la ferme pas tant que tu
echo utilises l'app - la fermer arrete le serveur.
echo.
echo Cette fenetre-ci, en revanche, peut etre fermee sans probleme.
echo.
pause
