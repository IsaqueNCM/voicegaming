# VoiceGaming_SWITCH.py - Microfone virtual, Soundboard DINÂMICO, Baixa Latência e System Tray
# 
# NECESSÁRIO: Instalar numpy, sounddevice, soundfile, PyQt5, scipy E keyboard
# pip install numpy sounddevice soundfile PyQt5 scipy keyboard
#
import sys
import os
import numpy as np
import sounddevice as sd
import soundfile as sf
import threading
import queue
import json 
import keyboard 
from PyQt5 import QtWidgets, QtCore, QtGui
from scipy.signal import resample_poly

# ==================== CONFIGURAÇÕES GLOBAIS (AJUSTADO PARA BAIXA LATÊNCIA) ====================
# Taxa de amostragem (44100 Hz recomendado para USB/VB-CABLE)
SAMPLERATE = 44100
# CORREÇÃO: BLOCKSIZE reduzido para BAIXA LATÊNCIA (~11ms).
BLOCKSIZE = 512 
CHANNELS = 1
CONFIG_FILE = 'config.json'

# Filas e flags de controle
output_queue = queue.Queue(maxsize=100)
mode_voice = True      # True = passa microfone, False = toca música/soundboard
playing_music = False
stop_music_event = threading.Event()
# Instâncias de streams
input_stream = None
output_stream = None
# Armazena o fator de volume para música/soundboard
music_volume_factor = 0.8 
# Novo: Armazena o fator de volume para o microfone principal
mic_volume_factor = 1.0 

# NOVO: Variáveis para controle do Soundboard (Toggle)
current_soundboard_key = None     # Armazena qual atalho de soundboard está tocando
soundboard_stop_event = None      # Evento para forçar a parada do soundboard

# Variável global para armazenar os atalhos dinâmicos
# Ex: {'home+1': 'caminho/musica1.mp3', 'home+k': 'caminho/musica2.py'}
SOUNDBOARD_SHORTCUTS = {} 

# ==================== FUNÇÕES DE PERSISTÊNCIA ====================

def save_config(input_idx, output_idx, volume, shortcuts):
    """Salva a configuração atual em um arquivo JSON."""
    config = {
        'input_device_index': input_idx,
        'output_device_index': output_idx,
        'volume_level': volume,
        'soundboard_shortcuts': shortcuts
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Erro ao salvar configuração: {e}", file=sys.stderr)

def load_config():
    """Carrega a configuração de um arquivo JSON. Retorna {} se não existir ou houver erro."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao carregar configuração ({CONFIG_FILE}): {e}", file=sys.stderr)
            return {}
    return {} 

# ==================== CALLBACKS E THREADS DE ÁUDIO ====================

def input_callback(indata, frames, time, status):
    """
    Callback para o seu Microfone Real (INPUT). 
    """
    global mode_voice
    
    # Checagem de status removida para evitar mensagens de erro desnecessárias.

    if mode_voice and not playing_music:
        # Aplica o fator de volume antes de colocar na fila
        processed_data = indata * mic_volume_factor
        output_queue.put(processed_data.copy())

def output_callback(outdata, frames, time, status):
    """
    Callback para a saída (OUTPUT) - Microfone Virtual.
    """
        
    try:
        # Tenta obter dados da fila
        data = output_queue.get_nowait()
    except queue.Empty:
        # Não há dados, envia silêncio para a saída
        data = np.zeros((frames, CHANNELS), dtype='float32')

    # Garantia que o bloco de saída tem o tamanho correto.
    if data.shape[0] != frames:
        data = np.zeros((frames, CHANNELS), dtype='float32') 

    # Normalização de pico (Limiter simples)
    peak = np.max(np.abs(data))
    if peak > 0.95: 
        data = data * (0.95 / peak)
    
    outdata[:] = data

def play_audio_thread(filepath, is_music, status_update_callback, hotkey=None, stop_event=None):
    """Função genérica para tocar áudio em thread separada."""
    global playing_music, mode_voice, current_soundboard_key
    
    if not os.path.exists(filepath):
        status_update_callback("Erro: Arquivo não encontrado.", "red")
        
        # Lógica de limpeza caso o arquivo falhe ao iniciar
        if not is_music:
            mode_voice = True 
            current_soundboard_key = None
            # Limpa a fila, pois a reprodução falhou
            while not output_queue.empty():
                try: output_queue.get_nowait()
                except: pass
        return


    # MÚSICA PRINCIPAL
    if is_music:
        stop_music_event.clear()
        playing_music = True
        mode_voice = False 
        status_update_callback("MÚSICA Principal: Tocando → voz pausada", "#ff0040")
        
    # SOUNDBOARD
    else:
        # Garante que o microfone seja pausado
        mode_voice = False 
        status_update_callback(f"Soundboard: Tocando atalho {hotkey} ({os.path.basename(filepath)}) → voz pausada", "#700070")


    try:
        audio, sr = sf.read(filepath, dtype='float32')
        
        # Converte para mono
        if len(audio.shape) > 1 and audio.shape[1] > 1:
            audio = np.mean(audio, axis=1)
            
        # Resample para a taxa de amostragem do stream (CORRIGINDO A VELOCIDADE)
        if sr != SAMPLERATE:
            # Resample poly é mais seguro e rápido para o áudio
            audio = resample_poly(audio, SAMPLERATE, sr).astype(np.float32)
            
        # Aplica volume
        peak = np.max(np.abs(audio))
        if peak > 0:
            # Aplica o fator de volume global
            audio = audio / peak * music_volume_factor

        # Loop de reprodução
        pos = 0
        
        # Condição de parada dinâmica (Música Principal OU Soundboard)
        stop_condition = lambda: (is_music and stop_music_event.is_set()) or (not is_music and stop_event.is_set())

        while pos < len(audio) and not stop_condition():
            end = pos + BLOCKSIZE
            block = audio[pos:end]
            
            if len(block) < BLOCKSIZE:
                # Preenche com silêncio se o bloco final for menor que BLOCKSIZE
                block = np.pad(block, (0, BLOCKSIZE - len(block)))
            
            # Garante que o shape é (frames, 1) antes de colocar na fila
            if block.ndim == 1:
                block = block.reshape(-1, 1)
            
            # Coloca na fila de saída (buffer). O blocking put() sincroniza o timing.
            output_queue.put(block)
            
            pos = end

    except Exception as e:
        status_update_callback(f"Erro no áudio: {e}", "red")
    
    finally:
        if is_music:
            playing_music = False
            mode_voice = True 
            status_update_callback("Música parada/finalizada → voltando sua voz...", "#00ff00")
            
            # Limpa a fila de output para evitar loop de silêncio (apenas se a música principal parou)
            while not output_queue.empty():
                try: output_queue.get_nowait()
                except: pass
        
        # FINAL: Lógica de limpeza do soundboard
        else:
            is_cancelled = stop_event.is_set()
            
            # Limpa a fila de output para evitar loop de silêncio
            while not output_queue.empty():
                try: output_queue.get_nowait()
                except: pass
                
            mode_voice = True 
            current_soundboard_key = None # Libera a chave
            
            if is_cancelled:
                status_update_callback(f"Soundboard ({hotkey}) CANCELADO → voltando sua voz...", "#00ff00")
            else:
                status_update_callback(f"Soundboard ({hotkey}) finalizado → voltando sua voz...", "#00ff00")


# ==================== INTERFACE GRÁFICA (PyQt5) ====================

class VoiceGamingSWITCH(QtWidgets.QMainWindow):
    status_signal = QtCore.pyqtSignal(str, str)
    # SIGNAL para mover a execução do atalho do teclado para a thread principal da PyQt
    hotkey_signal = QtCore.pyqtSignal(str) 

    def __init__(self):
        super().__init__()
        self.config = load_config()
        
        # Inicializa atalhos dinâmicos
        global SOUNDBOARD_SHORTCUTS
        SOUNDBOARD_SHORTCUTS = self.config.get('soundboard_shortcuts', {})
        
        # Carrega paths da música principal (key '0')
        self.musica_path = SOUNDBOARD_SHORTCUTS.get('0')

        self.device_info = sd.query_devices()
        self.input_devices = [d for d in self.device_info if d['max_input_channels'] > 0]
        self.output_devices = [d for d in self.device_info if d['max_output_channels'] > 0]
        
        self.volume_level = self.config.get('volume_level', 80)
        global music_volume_factor
        music_volume_factor = self.volume_level / 100.0
        
        self.mic_level = 100 # Volume do microfone padrão em 100%
        global mic_volume_factor
        mic_volume_factor = self.mic_level / 100.0
        
        self.setWindowTitle("VoiceGaming SWITCH - Voz ⇄ Áudio (Latência Baixa)")
        self.setGeometry(200, 100, 560, 850) 
        self.setStyleSheet("background:#0a0a0a; color:#00ff00; font-family: 'Consolas', monospace;")
        
        self.setup_tray_icon()
        self.setup_ui()
        self.status_signal.connect(self.update_status_ui)
        # CONEXÃO CRÍTICA: Faz a chamada do atalho ser executada na thread da GUI
        self.hotkey_signal.connect(self.play_soundboard_audio) 
        
        self.setup_hotkeys()
        
    # --- System Tray Implementation ---
    def setup_tray_icon(self):
        """Configura o ícone na bandeja do sistema (System Tray)."""
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon))
        self.tray_icon.setToolTip("VoiceGaming SWITCH")

        menu = QtWidgets.QMenu(self)
        
        # Ação para mostrar/restaurar
        restore_action = menu.addAction("Mostrar Janela")
        restore_action.triggered.connect(self.showNormal)
        
        # Ação para iniciar/parar streams
        self.start_stop_action = menu.addAction("Iniciar Streams")
        self.start_stop_action.triggered.connect(self.toggle_streams_from_tray)
        
        # Separador e Sair
        menu.addSeparator()
        exit_action = menu.addAction("Sair")
        exit_action.triggered.connect(self.exit_app)
        
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()
        
        # Ao clicar duas vezes no ícone, mostra a janela
        self.tray_icon.activated.connect(self.tray_activated)

    def tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.DoubleClick:
            self.showNormal()

    def toggle_streams_from_tray(self):
        """Função para ser chamada pelo menu do System Tray."""
        if input_stream and output_stream:
            self.stop_streams()
        else:
            self.start_streams()
            
    def exit_app(self):
        """Garante que a aplicação feche corretamente, parando streams e salvando config."""
        self.stop_streams()
        # Salva a configuração antes de sair
        self.save_current_config()
        self.tray_icon.hide()
        # Necessário dar um tempo para os streams fecharem antes de sair do loop principal
        QtCore.QTimer.singleShot(100, QtWidgets.QApplication.quit)


    def closeEvent(self, event):
        """Intercepta o clique no 'X' para minimizar para a bandeja."""
        if self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()
            
    def save_current_config(self):
        """Salva a configuração atual de streams, volume e atalhos."""
        input_idx = self.input_combo.currentData() if self.input_combo.currentData() is not None else -1
        output_idx = self.output_combo.currentData() if self.output_combo.currentData() is not None else -1
        
        # Atualiza o path da música principal
        if self.musica_path:
            SOUNDBOARD_SHORTCUTS['0'] = self.musica_path
        else:
            SOUNDBOARD_SHORTCUTS.pop('0', None) # Remove se não houver path
            
        # Filtra os paths que não são None
        valid_shortcuts = {k: v for k, v in SOUNDBOARD_SHORTCUTS.items() if v}
        
        save_config(input_idx, output_idx, self.volume_level, valid_shortcuts)

    # --- UI Setup and Components ---
    def setup_ui(self):
        """Configura todos os elementos da interface."""
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        title = QtWidgets.QLabel("VoiceGaming SWITCH")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 36px; font-weight: bold; color:#00ff88; margin-top: 10px;")
        layout.addWidget(title)

        info = QtWidgets.QLabel(
            f"✅ **BAIXA LATÊNCIA CONFIGURADA.**\n"
            f"⚠️ Selecione o **CABLE Input** (Saída) e use o **CABLE Output** no Discord/Jogo.\n"
            f"Taxa de Amostragem (SR): {SAMPLERATE} Hz | Bloco (BS): {BLOCKSIZE}."
        )
        info.setWordWrap(True)
        info.setStyleSheet("background:#111; padding:15px; border-radius:10px; border: 1px solid #00ffff; color: #00ffff;")
        layout.addWidget(info)
        
        # Streams Selection
        layout.addWidget(QtWidgets.QLabel("\n1. Seleção de Dispositivos:"))
        
        # --- Combobox Input ---
        input_combo, input_wrapper = self._create_device_combo(self.input_devices, 'max_input_channels', 'Microfone Real')
        self.input_combo = input_combo
        default_in = self.config.get('input_device_index', sd.default.device[0] if sd.default.device else -1)
        self._set_default_device(self.input_combo, default_in)
        layout.addWidget(input_wrapper)

        # --- Combobox Output ---
        output_combo, output_wrapper = self._create_device_combo(self.output_devices, 'max_output_channels', 'Saída CABLE Input')
        self.output_combo = output_combo
        default_out = self.config.get('output_device_index', sd.default.device[1] if sd.default.device else -1)
        self._set_default_device(self.output_combo, default_out)
        layout.addWidget(output_wrapper)
        
        # Start/Stop Button
        self.btn_start_stop = QtWidgets.QPushButton("INICIAR AUDIO STREAMS (Ativar)")
        self.btn_start_stop.clicked.connect(self.toggle_streams)
        self.btn_start_stop.setStyleSheet("padding:15px; background:#0040ff; color:white; font-weight:bold; font-size:18px; border-radius: 10px;")
        layout.addWidget(self.btn_start_stop)
        
        # --- Volume Control Músicas/Soundboard ---
        volume_layout = QtWidgets.QHBoxLayout()
        volume_layout.addWidget(QtWidgets.QLabel("Volume Músicas/Soundboard:"))
        
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.volume_level)
        self.volume_slider.setSingleStep(5)
        self.volume_slider.valueChanged.connect(self.update_music_volume)
        volume_layout.addWidget(self.volume_slider)
        
        self.label_volume = QtWidgets.QLabel(f"{self.volume_level}%")
        self.label_volume.setFixedWidth(50)
        volume_layout.addWidget(self.label_volume)
        
        layout.addLayout(volume_layout)
        
        # --- Volume Control Microfone ---
        mic_volume_layout = QtWidgets.QHBoxLayout()
        mic_volume_layout.addWidget(QtWidgets.QLabel("Volume Microfone Principal:"))
        
        self.mic_volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.mic_volume_slider.setRange(0, 100)
        self.mic_volume_slider.setValue(self.mic_level)
        self.mic_volume_slider.setSingleStep(5)
        self.mic_volume_slider.valueChanged.connect(self.update_mic_volume)
        mic_volume_layout.addWidget(self.mic_volume_slider)
        
        self.label_mic_volume = QtWidgets.QLabel(f"{self.mic_level}%")
        self.label_mic_volume.setFixedWidth(50)
        mic_volume_layout.addWidget(self.label_mic_volume)
        
        layout.addLayout(mic_volume_layout)
        
        
        # 2. Música Principal (Hotkey 'HOME + 0')
        layout.addWidget(QtWidgets.QLabel(" ")) 
        layout.addWidget(QtWidgets.QLabel("--- 2. Música Principal (Alterna/Pausa Voz) | Hotkey: HOME + 0 ---"))

        btn_music = QtWidgets.QPushButton("Selecionar Áudio Principal")
        btn_music.clicked.connect(lambda: self.add_shortcut_ui('0'))
        btn_music.setStyleSheet("padding:10px; background:#00ff00; color:black; font-weight:bold; border-radius: 8px;")
        layout.addWidget(btn_music)

        self.label_musica = QtWidgets.QLabel(self._get_shortcut_label_text('0'))
        self.label_musica.setStyleSheet("padding:10px; background:#222; border-radius:8px;")
        layout.addWidget(self.label_musica)

        self.btn_play = QtWidgets.QPushButton("TOCAR MÚSICA (Alternar Voz)")
        self.btn_play.setStyleSheet("padding:15px; font-size:18px; background:#4000ff; color:white; font-weight: bold; border-radius: 10px;")
        self.btn_play.clicked.connect(lambda: self.toggle_music('0'))
        self.btn_play.setEnabled(False) 
        layout.addWidget(self.btn_play)
        
        # 3. Soundboard Dinâmico
        layout.addWidget(QtWidgets.QLabel(" ")) 
        layout.addWidget(QtWidgets.QLabel("--- 3. Soundboard Dinâmico (Alterna Voz: Pausa/Cancela) ---"))
        
        # Container para os atalhos
        self.soundboard_container = QtWidgets.QVBoxLayout()
        layout.addLayout(self.soundboard_container)

        # Botão para Adicionar Novo Atalho
        btn_add_shortcut = QtWidgets.QPushButton("ADICIONar NOVO ATALHO PERSONALIZADO")
        btn_add_shortcut.clicked.connect(lambda: self.add_shortcut_dialog())
        btn_add_shortcut.setStyleSheet("padding:10px; background:#444; color:#fff; font-weight:bold; border-radius: 8px;")
        layout.addWidget(btn_add_shortcut)

        # Status
        self.status = QtWidgets.QLabel("Status: Pressione 'INICIAR AUDIO STREAMS'")
        self.status.setStyleSheet("color:#ffdd00; font-size:18px; padding:10px; background:#111; border-radius: 8px;")
        layout.addWidget(self.status)

        layout.addStretch(1) 
        
        # Atualiza a UI dinamicamente
        self._update_soundboard_ui_from_config()
        self._update_start_stop_ui()

    # --- UI Helpers ---
    def _create_device_combo(self, device_list, channel_key, label_text):
        """
        Cria e preenche um QComboBox para dispositivos de áudio.
        
        RETORNA: O QComboBox E o QWidget wrapper para evitar o erro de deleção.
        """
        
        box = QtWidgets.QWidget()
        h_layout = QtWidgets.QHBoxLayout(box)
        h_layout.setContentsMargins(0, 0, 0, 0)
        
        label = QtWidgets.QLabel(label_text)
        label.setFixedWidth(120)
        h_layout.addWidget(label)
        
        combo = QtWidgets.QComboBox()
        virtual_names = {"cable output", "cable input", "mixagem estéreo", "stereo mix", "what u hear", "vb-audio"}
        
        # Preenche com dispositivos virtuais primeiro
        for d in device_list:
            name_lower = d['name'].lower()
            if any(vn in name_lower for vn in virtual_names):
                 text = f"✨ VIRTUAL: {d['name']} (SR: {d['default_samplerate']:.0f} Hz)"
                 combo.addItem(text, d['index'])
                 
        # Depois, dispositivos físicos/padrão
        for d in device_list:
            name_lower = d['name'].lower()
            if not any(vn in name_lower for vn in virtual_names):
                text = f"{d['name']} (SR: {d['default_samplerate']:.0f} Hz)"
                combo.addItem(text, d['index'])
        
        h_layout.addWidget(combo)
        
        # RETORNA o QComboBox e o WIDGET wrapper
        return combo, box

    def _set_default_device(self, combo, default_index):
        """Tenta pré-selecionar o dispositivo padrão na ComboBox."""
        index = combo.findData(default_index)
        if index != -1:
            combo.setCurrentIndex(index)
            
    def update_music_volume(self, value):
        """Atualiza o volume da música e a label."""
        global music_volume_factor
        self.volume_level = value
        self.label_volume.setText(f"{value}%")
        music_volume_factor = value / 100.0
        
    def update_mic_volume(self, value):
        """Atualiza o volume do microfone principal e a label."""
        global mic_volume_factor
        self.mic_level = value
        self.label_mic_volume.setText(f"{value}%")
        mic_volume_factor = value / 100.0


    @QtCore.pyqtSlot(str, str)
    def update_status_ui(self, message, color):
        """Atualiza a label de status na thread da UI."""
        self.status.setText(f"Status: {message}")
        self.status.setStyleSheet(f"color:{color}; font-size:18px; padding:10px; background:#111; border-radius: 8px;")

    def _get_shortcut_label_text(self, hotkey):
        """Retorna o nome do arquivo ou um texto padrão."""
        path = SOUNDBOARD_SHORTCUTS.get(hotkey)
        if path:
            return os.path.basename(path)
        return "Nenhum áudio configurado."

    def _clear_soundboard_container(self):
        """Limpa o layout dinâmico do Soundboard."""
        while self.soundboard_container.count():
            item = self.soundboard_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
    
    def _update_soundboard_ui_from_config(self):
        """Redesenha a seção do Soundboard com base em SOUNDBOARD_SHORTCUTS."""
        self._clear_soundboard_container()
        
        # 1. Atualiza a label da Música Principal
        self.label_musica.setText(self._get_shortcut_label_text('0'))
        if self.musica_path and input_stream and output_stream:
            self.btn_play.setEnabled(True)
        else:
            self.btn_play.setEnabled(False)
            
        # 2. Desenha os atalhos dinâmicos (exceto '0')
        sorted_keys = sorted([k for k in SOUNDBOARD_SHORTCUTS.keys() if k != '0'], key=lambda x: x.split('+')[-1])

        for hotkey in sorted_keys:
            path = SOUNDBOARD_SHORTCUTS[hotkey]
            
            # --- Cria o Widget de Atalho ---
            h_widget = QtWidgets.QWidget()
            h_layout = QtWidgets.QHBoxLayout(h_widget)
            h_layout.setContentsMargins(0, 5, 0, 5)

            # Botão de Play
            btn_play_sb = QtWidgets.QPushButton(f"▶️ Tocar/Parar | {hotkey.upper()}")
            # O botão roxo (soundboard)
            btn_play_sb.setStyleSheet("background:#700070; color:#fff; padding: 5px; font-weight: bold; border-radius: 5px;")
            btn_play_sb.setFixedWidth(200)
            btn_play_sb.clicked.connect(lambda checked, k=hotkey: self.play_soundboard_audio(k))
            btn_play_sb.setEnabled(input_stream is not None)
            h_layout.addWidget(btn_play_sb)

            # Label do Arquivo
            label_sb = QtWidgets.QLabel(os.path.basename(path))
            label_sb.setStyleSheet("padding:5px; background:#181818; border-radius:4px; font-size:12px;")
            h_layout.addWidget(label_sb)
            
            # Botão de Remover
            btn_remove = QtWidgets.QPushButton("X")
            btn_remove.setStyleSheet("background:#ff0000; color:white; font-weight:bold; border-radius: 5px; padding: 5px;")
            btn_remove.setFixedWidth(30)
            btn_remove.clicked.connect(lambda checked, k=hotkey: self.remove_shortcut(k))
            h_layout.addWidget(btn_remove)
            
            self.soundboard_container.addWidget(h_widget)

    # --- Lógica de Hotkeys e Soundboard ---
    
    def play_audio_via_hotkey(self, hotkey):
        """
        Função intermediária chamada pela thread do 'keyboard'.
        Ela EMITE o signal para mover a execução para a thread da GUI imediatamente (eliminando o lag do atalho).
        """
        self.hotkey_signal.emit(hotkey) 
        
    @QtCore.pyqtSlot(str)
    def play_soundboard_audio(self, hotkey):
        global current_soundboard_key, soundboard_stop_event # CORREÇÃO: Mover global para a primeira linha executável

        """
        Toca um áudio do soundboard ou o cancela (toggle).
        """
        
        # 1. LÓGICA DE CANCELAMENTO (Toggle)
        if hotkey == current_soundboard_key:
            if soundboard_stop_event and not soundboard_stop_event.is_set():
                # Aciona o stop, a limpeza e o mode_voice=True serão tratados na thread de áudio.
                soundboard_stop_event.set() 
                self.update_status_ui(f"Atalho {hotkey} CANCELADO. Aguarde voz voltar...", "#ffdd00")
                return

        # 2. LÓGICA DE PREVENÇÃO de CONFLITO (Com Música Principal ou Outro Soundboard)
        if playing_music:
            self.update_status_ui("Soundboard ignorado: Música principal já está tocando.", "orange")
            return
            
        if current_soundboard_key is not None:
             # Um áudio de soundboard está tocando, mas não é o mesmo que o usuário pressionou.
             self.update_status_ui(f"Soundboard ocupado ({current_soundboard_key.upper()}): Cancele o áudio atual primeiro.", "orange")
             return

        # 3. INICIA UM NOVO ÁUDIO
        path = SOUNDBOARD_SHORTCUTS.get(hotkey)
        
        if not path:
            self.update_status_ui(f"Nenhum áudio configurado para Hotkey {hotkey}", "orange")
            return
            
        if not input_stream or not output_stream:
            self.update_status_ui("Streams de áudio não iniciados. Ative-os primeiro!", "red")
            return

        # Cria e armazena o novo evento de parada e a chave
        # Não é necessário um 'global' extra aqui, pois já foi declarado no topo da função.
        soundboard_stop_event = threading.Event()
        current_soundboard_key = hotkey

        threading.Thread(
            target=play_audio_thread, 
            args=(path, False, self.status_signal.emit, hotkey, soundboard_stop_event), # Passa o novo evento
            daemon=True
        ).start()
        
    def setup_hotkeys(self):
        """Registra todos os hotkeys ativos."""
        # Desregistra tudo primeiro para evitar duplicatas
        keyboard.unhook_all()

        # Registra a música principal
        if SOUNDBOARD_SHORTCUTS.get('0'):
            # O toggle_music deve ser mantido direto para gerenciar a parada/início.
            keyboard.add_hotkey('home+0', lambda: self.toggle_music('0'))
            
        # Registra os atalhos dinâmicos (Soundboard)
        for hotkey in [k for k in SOUNDBOARD_SHORTCUTS.keys() if k != '0' and SOUNDBOARD_SHORTCUTS[k]]:
            # Usa a função wrapper que emite o signal
            keyboard.add_hotkey(hotkey, lambda k=hotkey: self.play_audio_via_hotkey(k)) 
            
        self.update_status_ui(f"Hotkeys ativas: {len(SOUNDBOARD_SHORTCUTS)}", "#00ff88")
        
    def add_shortcut_dialog(self):
        """Abre a caixa de diálogo para configurar um novo atalho."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Adicionar Novo Atalho")
        dialog.setStyleSheet("background:#111; color:#fff;")
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 1. Hotkey Input
        layout.addWidget(QtWidgets.QLabel("Hotkey (Ex: home+k, home+ctrl+f):"))
        self.hotkey_input = QtWidgets.QLineEdit()
        self.hotkey_input.setPlaceholderText("Pressione a combinação de teclas (Ex: HOME + K)")
        self.hotkey_input.setStyleSheet("padding: 8px; background:#222; border: 1px solid #00ff00;")
        layout.addWidget(self.hotkey_input)
        
        self.hotkey_input.installEventFilter(self)
        self.current_hotkey = None
        
        # 2. File Selection
        layout.addWidget(QtWidgets.QLabel("\nÁudio (.mp3, .wav, etc):"))
        self.file_path_label = QtWidgets.QLabel("Nenhum arquivo selecionado.")
        self.file_path_label.setStyleSheet("padding: 8px; background:#222;")
        layout.addWidget(self.file_path_label)
        
        btn_select_file = QtWidgets.QPushButton("Selecionar Arquivo de Áudio")
        btn_select_file.clicked.connect(self.select_file_for_shortcut)
        btn_select_file.setStyleSheet("padding: 8px; background:#0040ff; color:white;")
        layout.addWidget(btn_select_file)
        
        # 3. Save Button
        btn_save = QtWidgets.QPushButton("Salvar Atalho")
        btn_save.clicked.connect(lambda: self.save_new_shortcut(dialog))
        btn_save.setStyleSheet("padding: 10px; background:#00ff00; color:black; font-weight:bold;")
        layout.addWidget(btn_save)
        
        dialog.exec_()
        
    def eventFilter(self, source, event):
        """Captura a combinação de teclas digitada para o atalho."""
        if source == self.hotkey_input and event.type() == QtCore.QEvent.KeyPress:
            # Captura a tecla HOME como base, é necessária para ser global e não atrapalhar a digitação normal
            if event.key() == QtCore.Qt.Key_Home:
                self.hotkey_input.setText("home+")
                self.hotkey_input.setReadOnly(True) 
                return True
            
            if self.hotkey_input.isReadOnly() and event.key() != QtCore.Qt.Key_Return:
                # Se já digitou HOME, agora registra a combinação
                key_text = QtGui.QKeySequence(event.key()).toString().lower()
                
                # Trata as modificadoras que podem aparecer
                mod_str = ""
                if event.modifiers() & QtCore.Qt.ControlModifier: mod_str += "ctrl+"
                if event.modifiers() & QtCore.Qt.ShiftModifier: mod_str += "shift+"
                if event.modifiers() & QtCore.Qt.AltModifier: mod_str += "alt+"
                
                # A string do atalho deve ser 'home+key' (ou com modificadores)
                final_key = f"home+{mod_str}{key_text}".replace("home+home+", "home+")
                
                self.current_hotkey_dialog = final_key.strip('+')
                self.hotkey_input.setText(self.current_hotkey_dialog)
                return True
                
        return super().eventFilter(source, event)
    
    def select_file_for_shortcut(self):
        """Seleciona o arquivo de áudio para o novo atalho."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Selecionar Áudio para Atalho", "", "Áudio (*.mp3 *.wav *.ogg *.flac)")
        if path:
            self.shortcut_file_path = path
            self.file_path_label.setText(os.path.basename(path))

    def save_new_shortcut(self, dialog):
        """Salva o novo atalho e o arquivo na configuração global."""
        if not hasattr(self, 'current_hotkey_dialog') or not self.current_hotkey_dialog or self.current_hotkey_dialog == 'home+':
            self.update_status_ui("Erro: Defina uma hotkey válida (HOME + Tecla).", "red")
            return
            
        if not hasattr(self, 'shortcut_file_path') or not self.shortcut_file_path:
            self.update_status_ui("Erro: Selecione um arquivo de áudio.", "red")
            return

        global SOUNDBOARD_SHORTCUTS
        SOUNDBOARD_SHORTCUTS[self.current_hotkey_dialog] = self.shortcut_file_path
        
        self.update_status_ui(f"Atalho {self.current_hotkey_dialog} salvo. Reiniciando hotkeys...", "#00ff88")
        
        self._update_soundboard_ui_from_config()
        self.setup_hotkeys()
        
        dialog.accept()

    def remove_shortcut(self, hotkey):
        """Remove um atalho do soundboard."""
        global SOUNDBOARD_SHORTCUTS
        if hotkey in SOUNDBOARD_SHORTCUTS:
            del SOUNDBOARD_SHORTCUTS[hotkey]
            self.update_status_ui(f"Atalho {hotkey} removido. Reiniciando hotkeys...", "#ffdd00")
            self._update_soundboard_ui_from_config()
            self.setup_hotkeys()

    def add_shortcut_ui(self, key):
        """Seleciona o áudio para o atalho principal (key='0')."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Selecionar áudio principal", "", "Áudio (*.mp3 *.wav *.ogg *.flac)")
        
        if path:
            global SOUNDBOARD_SHORTCUTS
            self.musica_path = path
            SOUNDBOARD_SHORTCUTS[key] = path
            
            self.label_musica.setText(os.path.basename(path))
            
            if input_stream and output_stream:
                self.btn_play.setEnabled(True)

            self.update_status_ui(f"Áudio principal selecionado. Configuração salva ao fechar.", "#00ff00")
            self.setup_hotkeys()

    def toggle_music(self, key):
        """Inicia ou para a reprodução da música principal (key='0')."""
        global playing_music
        path = SOUNDBOARD_SHORTCUTS.get(key)
        
        if not path:
            self.update_status_ui("Escolha uma música principal primeiro!", "red")
            return
            
        if not input_stream or not output_stream:
            self.update_status_ui("Streams de áudio não iniciados. Ative-os primeiro!", "red")
            return

        if playing_music:
            stop_music_event.set()
            self.btn_play.setText("TOCAR MÚSICA (Alternar Voz)")
            self.update_status_ui("Parando música, aguarde...", "#ffdd00")
            self.btn_play.setEnabled(False) 
            # Reativa o botão após 500ms, mas só se a música realmente parou
            QtCore.QTimer.singleShot(500, lambda: self.btn_play.setEnabled(True) if not playing_music else None) 
        else:
            self.btn_play.setText("PARAR MÚSICA")
            threading.Thread(
                target=play_audio_thread, 
                args=(path, True, self.status_signal.emit), 
                daemon=True
            ).start()
            self.btn_play.setEnabled(True) 

    # --- Streams Control ---
    def _update_start_stop_ui(self):
        """Atualiza o estado visual dos botões Start/Stop."""
        is_active = input_stream is not None and output_stream is not None
        
        if is_active:
            text = "PARAR AUDIO STREAMS (Desativar)"
            style = "padding:15px; background:#ff0000; color:white; font-weight:bold; font-size:18px; border-radius: 10px;"
            status_text = "Streams ATIVOS. Latência Baixa Habilitada."
            status_color = "#00ff00"
            self.start_stop_action.setText("Parar Streams")
        else:
            text = "INICIAR AUDIO STREAMS (Ativar)"
            style = "padding:15px; background:#0040ff; color:white; font-weight:bold; font-size:18px; border-radius: 10px;"
            status_text = "Streams INATIVOS. Pressione 'INICIAR AUDIO STREAMS'."
            status_color = "#ffdd00"
            self.start_stop_action.setText("Iniciar Streams")

        self.btn_start_stop.setText(text)
        self.btn_start_stop.setStyleSheet(style)
        self.update_status_ui(status_text, status_color)
        
        # Atualiza a capacidade de clique dos botões do Soundboard
        for widget_item in self.soundboard_container.parentWidget().findChildren(QtWidgets.QPushButton):
             if widget_item.text().startswith("▶️"):
                 widget_item.setEnabled(is_active)
                 
        self.btn_play.setEnabled(is_active and bool(self.musica_path))

    def toggle_streams(self):
        """Alterna entre iniciar e parar os streams de áudio."""
        if input_stream and output_stream:
            self.stop_streams()
        else:
            self.start_streams()

    def stop_streams(self):
        """Para e fecha os streams de áudio."""
        global input_stream, output_stream, playing_music, mode_voice, current_soundboard_key, soundboard_stop_event
        
        if input_stream:
            input_stream.stop()
            input_stream.close()
            input_stream = None
            
        if output_stream:
            output_stream.stop()
            output_stream.close()
            output_stream = None
            
        # Garante que a música pare
        if playing_music:
            stop_music_event.set()
            playing_music = False
            
        # Garante que o soundboard pare
        if current_soundboard_key is not None and soundboard_stop_event is not None:
             soundboard_stop_event.set()
             current_soundboard_key = None
             
        mode_voice = True # Força a voz a voltar
            
        self._update_start_stop_ui()
        self.update_status_ui("Streams DESATIVADOS.", "#ff0000")
        
    def start_streams(self):
        """Inicia os streams de áudio do microfone real e da saída virtual."""
        global input_stream, output_stream
        
        try:
            input_device_index = self.input_combo.currentData()
            output_device_index = self.output_combo.currentData()
            
            # Validação
            if input_device_index is None or output_device_index is None or input_device_index == -1 or output_device_index == -1:
                 self.update_status_ui("ERRO: Selecione um microfone real e uma saída CABLE Input válidos.", "red")
                 return
                 
            input_stream = sd.InputStream(
                device=input_device_index,
                channels=CHANNELS,
                samplerate=SAMPLERATE,
                blocksize=BLOCKSIZE, # Baixa latência
                callback=input_callback
            )

            output_stream = sd.OutputStream(
                device=output_device_index,
                channels=CHANNELS,
                samplerate=SAMPLERATE,
                blocksize=BLOCKSIZE, # Baixa latência
                callback=output_callback
            )

            input_stream.start()
            output_stream.start()
            
            self._update_start_stop_ui()
            self.save_current_config()

        except Exception as e:
            self.update_status_ui(f"ERRO ao iniciar streams: {e}. Verifique as permissões ou se o driver está em uso.", "red")
            print(f"ERRO: {e}", file=sys.stderr)
            input_stream = None
            output_stream = None

# ==================== INICIALIZAÇÃO ====================

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False) # Necessário para o System Tray
    window = VoiceGamingSWITCH()
    window.show()
    sys.exit(app.exec_())