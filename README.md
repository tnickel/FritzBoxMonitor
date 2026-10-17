# Fritz!Box Solar Monitor - Desktop Widget

Ein modernes, leichtgewichtiges Windows-Desktop-Widget zur Überwachung von Balkonkraftwerken / Solaranlagen, die über **FRITZ!DECT 200 / 210** oder **FRITZ!Smart Energy 210** Steckdosen einspeisen.

## Funktionsumfang

1. **Echtzeit-Leistung der 3 Steckdosen:**
   - Obere Kacheln zeigen die aktuellen Watt-Werte der 3 Einspeisesteckdosen:
     - **Garage Süd** (z. B. `77 W`)
     - **Garage Ost** (z. B. `32 W`)
     - **Schuppen** (z. B. `104 W`)
   - Gesamte Einspeiseleistung hervorgehoben (z. B. `Aktuell: 213 W`).

2. **Tages- und Jahressumme:**
   - **Tagessumme:** Präzise Erfassung der heute erzeugten Energie in **kWh** (auch wenn der PC erst später am Tag eingeschaltet wurde).
   - **Jahressumme:** Anzeige der erzeugten Energie des laufenden Jahres (z. B. `2.595 kWh`) sowie Gesamt-Zählerstand (`9.824 kWh`).

3. **Live-Graph (auf Klick):**
   - Klick auf das **📈 Graph-Symbol** (oder auf die Tagessumme) öffnet ein separates Fenster mit dem Live- und Tagesverlauf aller 3 Steckdosen und der Gesamtkurve.

4. **Widget-Verhalten:**
   - **Feste Position oben rechts** auf dem Bildschirm (beachtet automatisch Bildschirmauflösung und Taskleiste).
   - Kein störendes „Immer im Vordergrund“, verhält sich wie ein natürliches Desktop-Widget.
   - Kein schwarzes Konsolen- oder Terminal-Fenster im Hintergrund.

5. **Einstellungen (Zahnrad ⚙️):**
   - Fritz!Box IP-Adresse (Standard: `192.168.178.1`)
   - Fritz!Box Kennwort
   - Aktualisierungsintervall (Standard: 60 Sekunden)
   - Steckdosen-Zuweisung

---

## Starten

Einfach die Datei **`start.bat`** (oder **`start_silent.vbs`**) per Doppelklick ausführen.
Das Widget startet lautlos im Hintergrund und platziert sich in der oberen rechten Ecke deines Monitors.
