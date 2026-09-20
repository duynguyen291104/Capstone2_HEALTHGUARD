import { CircleAlert, Inbox, LoaderCircle, X } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function getFocusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (element) => element.getClientRects().length > 0 && element.getAttribute("aria-hidden") !== "true",
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  loading?: boolean;
};

export function Button({
  variant = "primary",
  loading = false,
  className = "",
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`button button--${variant} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <LoaderCircle className="spin" size={18} aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string;
};

export function Field({ label, hint, error, id, className = "", ...props }: FieldProps) {
  const fieldId = id ?? props.name;
  const helpId = fieldId ? `${fieldId}-help` : undefined;
  return (
    <label className={`field ${className}`} htmlFor={fieldId}>
      <span className="field__label">{label}</span>
      <input
        id={fieldId}
        className={`field__control ${error ? "field__control--error" : ""}`}
        aria-invalid={Boolean(error)}
        aria-describedby={hint || error ? helpId : undefined}
        {...props}
      />
      {hint || error ? (
        <span id={helpId} className={error ? "field__error" : "field__hint"}>
          {error ?? hint}
        </span>
      ) : null}
    </label>
  );
}

export function SelectField({
  label,
  id,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className="field" htmlFor={id}>
      <span className="field__label">{label}</span>
      <select id={id} className="field__control field__select" {...props}>
        {children}
      </select>
    </label>
  );
}

export function TextareaField({
  label,
  id,
  hint,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hint?: string }) {
  const helpId = id ? `${id}-help` : undefined;
  return (
    <label className="field" htmlFor={id}>
      <span className="field__label">{label}</span>
      <textarea
        id={id}
        className="field__control field__textarea"
        aria-describedby={hint ? helpId : undefined}
        {...props}
      />
      {hint ? <span id={helpId} className="field__hint">{hint}</span> : null}
    </label>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action ? <div className="page-header__action">{action}</div> : null}
    </header>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon"><Inbox size={25} aria-hidden="true" /></span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--error" role="alert">
      <CircleAlert size={18} aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

export function SuccessNotice({ message }: { message: string }) {
  return <div className="notice notice--success" role="status">{message}</div>;
}

export function Modal({
  title,
  description,
  onClose,
  children,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const modalRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const modal = modalRef.current;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    let focusFrame = 0;

    if (modal) {
      focusFrame = window.requestAnimationFrame(() => {
        const preferredTarget = modal.querySelector<HTMLElement>(
          "[data-modal-initial-focus], [autofocus], .modal__body input:not([disabled]):not([type='hidden']), .modal__body select:not([disabled]), .modal__body textarea:not([disabled])",
        );
        preferredTarget?.focus();

        if (!preferredTarget) {
          (getFocusableElements(modal)[0] ?? modal).focus();
        }
      });
    }

    function handleKeyDown(event: KeyboardEvent) {
      const currentModal = modalRef.current;
      if (!currentModal) return;

      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }

      if (event.key !== "Tab") return;

      const focusableElements = getFocusableElements(currentModal);
      if (focusableElements.length === 0) {
        event.preventDefault();
        currentModal.focus();
        return;
      }

      const firstElement = focusableElements[0];
      const lastElement = focusableElements[focusableElements.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey && (activeElement === firstElement || !currentModal.contains(activeElement))) {
        event.preventDefault();
        lastElement.focus();
      } else if (!event.shiftKey && (activeElement === lastElement || !currentModal.contains(activeElement))) {
        event.preventDefault();
        firstElement.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={modalRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal__header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Đóng">
            <X size={20} aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">{children}</div>
      </section>
    </div>
  );
}

export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-list" aria-label="Đang tải">
      {Array.from({ length: rows }).map((_, index) => (
        <div className="skeleton-card" key={index} />
      ))}
    </div>
  );
}
