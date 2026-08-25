"""Windows-Start ohne Konsolenfenster; die Anwendungslogik bleibt in sts_tester."""

from pathlib import Path
import traceback

from sts_tester import main


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # Startfehler muessen auch ohne Konsole sichtbar bleiben.
        error_log = Path("sts_tester_error.log")
        with error_log.open("a", encoding="utf-8") as stream:
            stream.write("\nUnbehandelter Fehler beim Programmstart\n")
            traceback.print_exc(file=stream)
        try:
            from tkinter import messagebox

            messagebox.showerror("Start fehlgeschlagen", f"Details: {error_log.resolve()}")
        except Exception:
            pass
