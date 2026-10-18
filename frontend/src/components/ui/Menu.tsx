import { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Icon, type IconName } from "./Icon";

const MenuContext = createContext<{ close: () => void } | null>(null);

interface MenuProps {
  /** Accessible name of the trigger, for example "Actions for report.pdf". */
  label: string;
  children: ReactNode;
  /** Custom trigger content; defaults to a three-dot icon button. */
  trigger?: ReactNode;
  triggerClassName?: string;
}

/**
 * A menu button: arrow keys move between items, Escape closes and returns focus, and the list is
 * positioned against the viewport so no ancestor can clip it.
 */
export function Menu({ label, children, trigger, triggerClassName }: MenuProps) {
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState<CSSProperties>({ opacity: 0 });
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) button.current?.focus();
  }, []);

  useLayoutEffect(() => {
    if (!open || !button.current || !list.current) return;
    const anchor = button.current.getBoundingClientRect();
    const height = list.current.offsetHeight;
    const width = list.current.offsetWidth;
    const below = anchor.bottom + 4 + height <= window.innerHeight - 8;
    const left = Math.min(Math.max(8, anchor.right - width), window.innerWidth - width - 8);
    setStyle({ top: below ? anchor.bottom + 4 : Math.max(8, anchor.top - 4 - height), left, opacity: 1 });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    list.current?.querySelector<HTMLElement>('[role^="menuitem"]:not([disabled])')?.focus();
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!list.current?.contains(target) && !button.current?.contains(target)) setOpen(false);
    };
    const dismiss = () => setOpen(false);
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [open]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [...(list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([disabled])') ?? [])];
    const index = items.indexOf(document.activeElement as HTMLElement);
    const move = (next: number) => {
      event.preventDefault();
      items[(next + items.length) % items.length]?.focus();
    };
    if (event.key === "ArrowDown") move(index + 1);
    else if (event.key === "ArrowUp") move(index - 1);
    else if (event.key === "Home") move(0);
    else if (event.key === "End") move(items.length - 1);
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation(); // do not also close a dialog this menu sits in
      close(true);
    } else if (event.key === "Tab") setOpen(false);
  };

  return (
    <MenuContext.Provider value={{ close: () => close(true) }}>
      <button
        ref={button}
        type="button"
        className={triggerClassName ?? "icon-btn"}
        aria-label={label}
        title={trigger ? undefined : label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          setStyle({ opacity: 0 });
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setStyle({ opacity: 0 });
            setOpen(true);
          }
        }}
      >
        {trigger ?? <Icon name="more" />}
      </button>
      {open ? (
        <div ref={list} id={id} role="menu" aria-label={label} className="menu" style={style} onKeyDown={onKeyDown}>
          {children}
        </div>
      ) : null}
    </MenuContext.Provider>
  );
}

interface ItemProps {
  icon?: IconName;
  danger?: boolean;
  disabled?: boolean;
  children: ReactNode;
}

export function MenuItem({ icon, danger, disabled, checked, onSelect, children }: ItemProps & { onSelect: () => void; checked?: boolean }) {
  const menu = useContext(MenuContext);
  return (
    <button
      type="button"
      role={checked === undefined ? "menuitem" : "menuitemradio"}
      aria-checked={checked}
      className={`menu-item${danger ? " menu-item-danger" : ""}`}
      disabled={disabled}
      onClick={() => {
        menu?.close();
        onSelect();
      }}
    >
      {icon ? <Icon name={icon} size={16} /> : null}
      <span>{children}</span>
      {checked ? <Icon name="check" size={16} /> : null}
    </button>
  );
}

export function MenuLink({ icon, to, children }: ItemProps & { to: string }) {
  const menu = useContext(MenuContext);
  return (
    <Link role="menuitem" to={to} className="menu-item" onClick={() => menu?.close()}>
      {icon ? <Icon name={icon} size={16} /> : null}
      <span>{children}</span>
    </Link>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <div className="menu-label">{children}</div>;
}

export function MenuSeparator() {
  return <div className="menu-sep" role="separator" />;
}
