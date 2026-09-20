"use client";

import { Check, Copy, MailPlus, ShieldCheck, Trash2, UserRoundCog } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button, EmptyState, ErrorNotice, Field, Modal, PageHeader, SelectField, SkeletonList, SuccessNotice } from "@/components/ui";
import { careApi, eldersApi, getErrorMessage } from "@/lib/api";
import type { CaregiverAssignment, CareGroupMember, Elder, Invitation } from "@/lib/types";

function InviteForm({ onCreated, onClose }: { onCreated: (invitation: Invitation) => void; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!email.includes("@")) return setError("Hãy nhập email hợp lệ của người chăm sóc.");
    setSaving(true);
    setError("");
    try {
      onCreated(await careApi.invite(email.trim().toLowerCase()));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      {error ? <ErrorNotice message={error} /> : null}
      <Field label="Email người chăm sóc" type="email" autoComplete="email" placeholder="co.lan@email.com" value={email} onChange={(event) => setEmail(event.target.value)} hint="Người được mời phải đăng ký bằng đúng email này." autoFocus />
      <div className="info-box"><ShieldCheck size={18} /><p>Lời mời chỉ dùng một lần và sẽ hết hạn. Sau khi tham gia, bạn vẫn cần phân công họ cho từng người cao tuổi.</p></div>
      <div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Hủy</Button><Button type="submit" loading={saving}>Tạo lời mời</Button></div>
    </form>
  );
}

function InviteResult({ invitation, onClose }: { invitation: Invitation; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const link = invitation.invitation_url || `${typeof window === "undefined" ? "" : window.location.origin}/tham-gia?token=${encodeURIComponent(invitation.invitation_token)}`;

  async function copy() {
    await navigator.clipboard.writeText(link);
    setCopied(true);
  }

  return (
    <div className="form-stack">
      <SuccessNotice message={`Đã tạo lời mời cho ${invitation.email}`} />
      <div className="invite-link"><span>{link}</span><Button type="button" variant="secondary" onClick={copy}>{copied ? <Check size={17} /> : <Copy size={17} />}{copied ? "Đã sao chép" : "Sao chép"}</Button></div>
      <p className="muted">Gửi liên kết này trực tiếp cho đúng người chăm sóc. Không đăng công khai liên kết.</p>
      <Button type="button" onClick={onClose}>Hoàn tất</Button>
    </div>
  );
}

function AssignmentControl({
  member,
  elders,
  assignments,
  onAssignmentsChange,
}: {
  member: CareGroupMember;
  elders: Elder[];
  assignments: CaregiverAssignment[];
  onAssignmentsChange: (assignment: CaregiverAssignment | null, elderId: string, caregiverId: string) => void;
}) {
  const firstElderId = elders[0]?.id ?? "";
  const initialAssignment = assignments.find((item) => item.elder_id === firstElderId && item.caregiver_user_id === member.user_id);
  const [elderId, setElderId] = useState(firstElderId);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [canViewMedications, setCanViewMedications] = useState(initialAssignment?.can_view_medications ?? true);
  const [canConfirmDoses, setCanConfirmDoses] = useState(initialAssignment?.can_confirm_doses ?? true);
  const [canViewDiagnoses, setCanViewDiagnoses] = useState(initialAssignment?.can_view_diagnoses ?? false);
  const currentAssignment = assignments.find((item) => item.elder_id === elderId && item.caregiver_user_id === member.user_id);

  function selectElder(nextElderId: string) {
    setElderId(nextElderId);
    const assignment = assignments.find((item) => item.elder_id === nextElderId && item.caregiver_user_id === member.user_id);
    setCanViewMedications(assignment?.can_view_medications ?? true);
    setCanConfirmDoses(assignment?.can_confirm_doses ?? true);
    setCanViewDiagnoses(assignment?.can_view_diagnoses ?? false);
    setMessage("");
    setError("");
  }

  async function assign() {
    if (!elderId) return;
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const assignment = await careApi.assign(elderId, member.user_id, {
        can_view_medications: canViewMedications,
        can_confirm_doses: canConfirmDoses,
        can_view_diagnoses: canViewDiagnoses,
      });
      onAssignmentsChange(assignment, elderId, member.user_id);
      const elder = elders.find((item) => item.id === elderId);
      setMessage(`Đã phân công chăm sóc ${elder?.full_name ?? "hồ sơ này"}.`);
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function unassign() {
    if (!currentAssignment || !window.confirm(`Hủy phân công ${member.full_name} khỏi hồ sơ đã chọn?`)) return;
    setSaving(true);
    setMessage("");
    setError("");
    try {
      await careApi.unassign(elderId, member.user_id);
      onAssignmentsChange(null, elderId, member.user_id);
      setMessage("Đã hủy phân công khỏi hồ sơ này.");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  if (!elders.length) return <p className="muted">Hãy thêm người được chăm sóc trước khi phân công.</p>;

  return (
    <div className="assignment-control">
      <SelectField label="Phân công chăm sóc" value={elderId} onChange={(event) => selectElder(event.target.value)}>
        {elders.map((elder) => <option key={elder.id} value={elder.id}>{elder.full_name}</option>)}
      </SelectField>
      <fieldset className="permission-picker">
        <legend>Quyền được cấp</legend>
        <label><input type="checkbox" checked={canViewMedications} onChange={(event) => { setCanViewMedications(event.target.checked); if (!event.target.checked) setCanConfirmDoses(false); }} /> Xem lịch thuốc</label>
        <label><input type="checkbox" checked={canConfirmDoses} onChange={(event) => { setCanConfirmDoses(event.target.checked); if (event.target.checked) setCanViewMedications(true); }} /> Xác nhận cho uống</label>
        <label><input type="checkbox" checked={canViewDiagnoses} onChange={(event) => setCanViewDiagnoses(event.target.checked)} /> Xem bệnh đã chẩn đoán</label>
      </fieldset>
      <div className="assignment-actions">
        <Button type="button" variant="secondary" onClick={assign} loading={saving}>{currentAssignment ? "Cập nhật quyền" : "Cấp quyền"}</Button>
        {currentAssignment ? <Button type="button" variant="ghost" onClick={unassign} loading={saving}>Hủy phân công</Button> : null}
      </div>
      {message ? <small className="success-text">{message}</small> : null}
      {error ? <small className="error-text">{error}</small> : null}
    </div>
  );
}

export function CaregiversPage() {
  const [members, setMembers] = useState<CareGroupMember[]>([]);
  const [elders, setElders] = useState<Elder[]>([]);
  const [assignments, setAssignments] = useState<CaregiverAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const caregivers = useMemo(() => members.filter((member) => member.role === "CAREGIVER"), [members]);

  useEffect(() => {
    let active = true;
    Promise.all([careApi.members(), eldersApi.list()])
      .then(async ([memberList, elderList]) => {
        const assignmentLists = await Promise.all(elderList.map((elder) => careApi.assignments(elder.id)));
        return [memberList, elderList, assignmentLists.flat()] as const;
      })
      .then(([memberList, elderList, assignmentList]) => {
        if (!active) return;
        setMembers(memberList);
        setElders(elderList);
        setAssignments(assignmentList);
      })
      .catch((caught) => { if (active) setError(getErrorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function remove(member: CareGroupMember) {
    if (!window.confirm(`Thu hồi quyền của ${member.full_name} khỏi nhóm chăm sóc?`)) return;
    setRemoving(member.user_id);
    setError("");
    try {
      await careApi.removeMember(member.user_id);
      setMembers((current) => current.filter((item) => item.user_id !== member.user_id));
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setRemoving(null);
    }
  }

  function closeInvite() {
    setInviteOpen(false);
    setInvitation(null);
  }

  function changeAssignment(assignment: CaregiverAssignment | null, elderId: string, caregiverId: string) {
    setAssignments((current) => {
      const rest = current.filter((item) => !(item.elder_id === elderId && item.caregiver_user_id === caregiverId));
      return assignment ? [...rest, assignment] : rest;
    });
  }

  return (
    <>
      <PageHeader
        eyebrow="Thành viên & quyền"
        title="Người chăm sóc"
        description="Mời người chăm sóc bằng tài khoản riêng rồi phân công đúng hồ sơ họ cần hỗ trợ."
        action={<Button onClick={() => setInviteOpen(true)}><MailPlus size={18} /> Mời người chăm sóc</Button>}
      />
      <div className="info-strip"><ShieldCheck size={19} /><p>Mỗi người dùng tài khoản riêng để hệ thống ghi nhận chính xác ai đã thao tác. Bạn có thể thu hồi quyền bất cứ lúc nào.</p></div>
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <SkeletonList rows={2} /> : caregivers.length === 0 ? (
        <EmptyState title="Chưa có người chăm sóc" description="Tạo lời mời và gửi liên kết cho người được thuê chăm sóc bố mẹ." action={<Button onClick={() => setInviteOpen(true)}><MailPlus size={18} /> Tạo lời mời</Button>} />
      ) : (
        <div className="member-list">
          {caregivers.map((member) => (
            <article className="member-card" key={member.user_id}>
              <div className="member-card__identity">
                <span className="member-avatar"><UserRoundCog size={21} /></span>
                <div><h2>{member.full_name}</h2><p>{member.email}</p><span className="role-badge">Người chăm sóc</span></div>
              </div>
              <AssignmentControl member={member} elders={elders} assignments={assignments} onAssignmentsChange={changeAssignment} />
              <Button type="button" variant="ghost" className="member-card__remove" loading={removing === member.user_id} onClick={() => remove(member)}><Trash2 size={17} /> Thu hồi khỏi nhóm</Button>
            </article>
          ))}
        </div>
      )}
      {inviteOpen ? (
        <Modal title={invitation ? "Gửi liên kết mời" : "Mời người chăm sóc"} description={invitation ? "Liên kết chỉ dành cho đúng email đã nhập." : "Người chăm sóc sẽ tự tạo mật khẩu cho tài khoản của họ."} onClose={closeInvite}>
          {invitation ? <InviteResult invitation={invitation} onClose={closeInvite} /> : <InviteForm onCreated={setInvitation} onClose={closeInvite} />}
        </Modal>
      ) : null}
    </>
  );
}
