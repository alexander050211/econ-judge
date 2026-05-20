CTFd._internal.challenge.data = undefined;
CTFd._internal.challenge.renderer = null;
CTFd._internal.challenge.preRender = function () {};
CTFd._internal.challenge.render = null;
CTFd._internal.challenge.postRender = function () {};

CTFd.pages.challenge.submitChallenge = async function (challenge_id, _submission) {
  const file_input = document.getElementById("challenge-file");
  if (!file_input || !file_input.files || !file_input.files.length) {
    return { data: { status: "incorrect", message: "Please select a .dig file." } };
  }

  const fd = new FormData();
  fd.append("file", file_input.files[0]);

  const r = await fetch(
    `/api/v1/digital/challenges/${challenge_id}/attempt`,
    {
      method: "POST",
      body: fd,
      credentials: "same-origin",
    }
  );

  return await r.json();
};
