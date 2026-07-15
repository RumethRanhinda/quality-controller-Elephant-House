import socket
import sys

def main():
    print("========================================")
    print("      UART TERMINAL SIMULATOR")
    print("========================================")
    print("The gui_main.py script is now automatically listening for these")
    print("UDP packets if the physical STM32 serial port is missing.")
    print("")
    print("Press a number and hit Enter to simulate an STM32 button:")
    print("  1: Up")
    print("  2: Down")
    print("  3: Left")
    print("  4: Right")
    print("  5: OK (Center)")
    print("  6: Start/End")
    print("========================================")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    while True:
        try:
            val = input("Send Button ID > ").strip()
            if val in ['1', '2', '3', '4', '5', '6']:
                print(f"  [!] Waiting 2 seconds... CLICK ON THE GUI WINDOW NOW!")
                import time
                time.sleep(2)
                sock.sendto(val.encode('utf-8'), ('127.0.0.1', 12345))
                print(f"  -> Sent UART Byte: 0x0{val}")
            else:
                print("  ! Invalid button ID. Enter 1-6.")
        except KeyboardInterrupt:
            print("\nExiting Simulator...")
            sys.exit(0)

if __name__ == '__main__':
    main()
