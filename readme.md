# 🎤 VoiceGaming SWITCH 🎶

**Microfone Virtual Dinâmico, Soundboard de Baixa Latência e System Tray**

Este sistema em Python é uma solução completa para gamers e streamers que precisam de controle avançado sobre a entrada de áudio. Ele transforma seu microfone real em um microfone virtual (usando VB-CABLE ou similar) e permite alternar instantaneamente entre a sua voz e a reprodução de áudio (músicas ou soundboard) com atalhos de teclado configuráveis.

## ✨ Recursos Principais

* **Alternância de Áudio (SWITCH):** Alterna automaticamente entre a **sua voz** e o **áudio de soundboard/música** ao pressionar um atalho. Enquanto o áudio toca, sua voz é pausada, eliminando conflitos e ruídos indesejados.
* **Baixa Latência Crítica:** Configurado com `SAMPLERATE = 44100` Hz e `BLOCKSIZE = 512` para garantir uma latência de áudio extremamente baixa (cerca de 11ms), essencial para comunicação em jogos.
* **Soundboard Dinâmico:** Crie e gerencie atalhos de teclado (`HOME + Tecla`) para tocar múltiplos arquivos de áudio sob demanda.
* **Controle de Música Principal:** Defina um áudio principal com o atalho `HOME + 0` para tocar/pausar a qualquer momento.
* **Volume Independente:** Controle o volume da **música/soundboard** e do **microfone principal** separadamente.
* **Interface Gráfica (PyQt5):** Interface de usuário intuitiva para seleção de dispositivos, ajuste de volume e gerenciamento de atalhos.
* **System Tray:** Minimiza para a bandeja do sistema, permitindo que o sistema de áudio continue rodando em segundo plano sem a janela principal.

---

## 🛠️ Requisitos e Instalação

### 1. Requisitos de Áudio

Para que o sistema funcione como um microfone virtual, você **DEVE** ter um cabo de áudio virtual instalado no seu sistema.

* **Recomendado:** [VB-Audio Virtual Cable (VB-CABLE)](https://vb-audio.com/Cable/)

### 2. Instalação de Dependências

O sistema requer várias bibliotecas Python. Certifique-se de que você está usando uma **versão padrão** e funcional do Python (evitando builds incompletas que causam erros como `ModuleNotFoundError: No module named 'audioop'`).

Execute o comando a seguir no seu terminal (PowerShell ou CMD):

```bash
py -m pip install numpy sounddevice soundfile PyQt5 scipy keyboard pydub